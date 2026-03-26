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
    this._settings = {};
    // Day/Night energy accumulator (session-based, persisted to settings)
    this._energyDayWs = 0;    // watt-seconds accumulated during day
    this._energyNightWs = 0;  // watt-seconds accumulated during night
    this._lastAccumTs = null;  // timestamp of last accumulation
    this._lastAccumSaveTs = 0; // last time accum was saved to settings
    this._accumDate = null;    // date string (YYYY-MM-DD) for reset detection
  }

  set hass(hass) {
    // Auto-reload on HA reconnect (after restart / power loss)
    if (this._wasDisconnected && hass.connected) {
      console.log('[SmartingHOME] HA reconnected after disconnect — auto-reloading panel...');
      if (!this._reloadScheduled) {
        this._reloadScheduled = true;
        setTimeout(() => location.reload(), 2000);
      }
      return;
    }
    if (hass.connected === false) {
      this._wasDisconnected = true;
      console.log('[SmartingHOME] HA connection lost — waiting for reconnect...');
    }
    this._hass = hass;
    this._ensureSubscriptions();
    if (!this.shadowRoot.querySelector(".panel-container")) {
      // DOM lost (HA navigated away and back) — re-render
      this._render();
      this._loadSettings();
    } else {
      this._updateAll();
    }
  }
  set panel(p) { this._panel = p; }
  set narrow(n) { this._narrow = n; }

  connectedCallback() {
    this._render();
    this._loadSettings();
    const wrap = this.shadowRoot.querySelector('.flow-wrapper');
    if (wrap && window.ResizeObserver) {
      this._ro = new ResizeObserver(() => this._updateSvgPaths());
      this._ro.observe(wrap);
      setTimeout(() => this._updateSvgPaths(), 50);
    }
    this._fsHandler = () => {
      this._isFullscreen = !!(document.fullscreenElement||document.webkitFullscreenElement||document.mozFullScreenElement||document.msFullscreenElement);
      const btn = this.shadowRoot.querySelector(".fullscreen-btn");
      if (btn) btn.textContent = this._isFullscreen ? "⊡ Zamknij" : "⊞ Pełny ekran";
    };
    ["fullscreenchange","webkitfullscreenchange","mozfullscreenchange","MSFullscreenChange"].forEach(ev => {
      document.addEventListener(ev, this._fsHandler);
    });
    // Reconnect subscriptions when tab becomes visible again
    this._visHandler = () => {
      if (!document.hidden && this._hass) {
        // Re-render if DOM was lost while tab was hidden
        if (!this.shadowRoot.querySelector('.panel-container')) {
          this._render();
          this._loadSettings();
        }
        // Restart polling interval (browsers kill setInterval in background tabs)
        if (this._interval) clearInterval(this._interval);
        this._interval = setInterval(() => { if (this._hass) this._updateAll(); }, 5000);
        this._ensureSubscriptions();
        this._updateAll();
      }
    };
    document.addEventListener('visibilitychange', this._visHandler);
    // Persist day/night accumulators when page is being closed
    this._unloadHandler = () => {
      if (this._energyDayWs > 0 || this._energyNightWs > 0) {
        this._savePanelSettings({
          _accum_day_ws: Math.round(this._energyDayWs),
          _accum_night_ws: Math.round(this._energyNightWs),
          _accum_date: new Date().toISOString().slice(0, 10)
        });
      }
    };
    window.addEventListener('beforeunload', this._unloadHandler);
  }
  disconnectedCallback() {
    if (this._interval) clearInterval(this._interval);
    if (this._watchdog) clearInterval(this._watchdog);
    if (this._ro) this._ro.disconnect();
    if (this._visHandler) document.removeEventListener('visibilitychange', this._visHandler);
    if (this._fsHandler) {
      ["fullscreenchange","webkitfullscreenchange","mozfullscreenchange","MSFullscreenChange"].forEach(ev => {
        document.removeEventListener(ev, this._fsHandler);
      });
    }
    if (this._unloadHandler) window.removeEventListener('beforeunload', this._unloadHandler);
    // Unsubscribe all event subscriptions
    if (this._cronSub) { try { this._cronSub(); } catch(e) {} this._cronSub = null; }
    if (this._actionStateSub) { try { this._actionStateSub(); } catch(e) {} this._actionStateSub = null; }
    if (this._testSub) { try { this._testSub(); } catch(e) {} this._testSub = null; }
  }

  _toggleFullscreen() {
    const c = this.shadowRoot.querySelector(".panel-container");
    const nativeFS = document.fullscreenElement||document.webkitFullscreenElement||document.mozFullScreenElement||document.msFullscreenElement;
    if (!nativeFS && !this._isFullscreen) {
      // Enter fullscreen
      const rfs = c.requestFullscreen||c.webkitRequestFullscreen||c.mozRequestFullScreen||c.msRequestFullscreen;
      if (rfs) rfs.call(c).catch(()=>{});
      else { c.classList.add("css-fullscreen"); this._isFullscreen = true; const btn = this.shadowRoot.querySelector(".fullscreen-btn"); if(btn) btn.textContent="⊡ Zamknij"; }
    } else {
      // Exit fullscreen
      if (nativeFS) {
        const efs = document.exitFullscreen||document.webkitExitFullscreen||document.mozCancelFullScreen||document.msExitFullscreen;
        if (efs) efs.call(document).catch(()=>{});
      }
      c.classList.remove("css-fullscreen"); this._isFullscreen = false;
      const btn = this.shadowRoot.querySelector(".fullscreen-btn"); if(btn) btn.textContent="⊞ Pełny ekran";
    }
  }
  _toggleHaSidebar() {
    const ev = new Event("hass-toggle-menu", { bubbles: true, composed: true });
    this.dispatchEvent(ev);
  }
  _switchTab(tab) {
    this._activeTab = tab;
    this.shadowRoot.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
    this.shadowRoot.querySelectorAll(".tab-content").forEach(c => c.classList.toggle("active", c.dataset.tab === tab));
    if (tab === 'winter') { this._initWinterTab(); this._loadWinterData(); }
    if (tab === 'wind') { this._initWindTab(); this._loadWindData(); this._fetchWindHistoricalStats(); }
    if (tab === 'hems') { this._updateHEMSArbitrage(); }
    if (tab === 'history') { this._updateHistoryTab(); }
    if (tab === 'autopilot') { this._updateAutopilot(); }
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
    grid_import_today:"sensor.grid_export_daily", grid_export_today:"sensor.grid_import_daily",
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

  /* ── Force Charge/Discharge Buttons ────────── */
  _executeForceAction(type) {
    const serviceMap = {
      charge: 'force_charge', discharge: 'force_discharge',
      stop_charge: 'stop_force_charge', stop_discharge: 'stop_force_discharge',
      emergency_stop: 'emergency_stop',
    };
    const colorMap = {
      charge: '46,204,113', discharge: '231,76,60',
      stop_charge: '245,158,11', stop_discharge: '245,158,11',
      emergency_stop: '231,76,60',
    };
    const btnIdMap = {
      charge: 'btn-force-charge', discharge: 'btn-force-discharge',
      stop_charge: 'btn-stop-charge', stop_discharge: 'btn-stop-discharge',
      emergency_stop: 'btn-emergency-stop',
    };
    const btnEl = this.shadowRoot.getElementById(btnIdMap[type] || `btn-force-${type}`);
    const statusEl = this.shadowRoot.getElementById(`fc-${type}-status`);
    if (!this._hass) return;

    const service = serviceMap[type] || `force_${type}`;
    const color = colorMap[type] || '100,100,100';

    // ── VISUAL: button press animation ──
    if (btnEl) {
      btnEl.style.transform = 'scale(0.93)';
      btnEl.style.boxShadow = `0 0 12px rgba(${color},0.5)`;
      setTimeout(() => { btnEl.style.transform = 'scale(1)'; }, 150);
      btnEl.disabled = true; btnEl.style.opacity = '0.6';
    }
    if (statusEl) {
      statusEl.textContent = '⏳ Wysyłanie...';
      statusEl.style.color = '#f59e0b';
    }

    this._hass.callService('smartinghome', service, {}).then(() => {
      const now = new Date().toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      if (statusEl) { statusEl.textContent = `✅ Wykonano ${now}`; statusEl.style.color = '#2ecc71'; }
      if (btnEl) {
        btnEl.disabled = false; btnEl.style.opacity = '1';
        btnEl.style.boxShadow = `0 0 20px rgba(${color},0.7)`;
        setTimeout(() => { btnEl.style.boxShadow = 'none'; }, 1500);
      }
    }).catch(err => {
      if (statusEl) { statusEl.textContent = `❌ Błąd: ${err.message || err}`; statusEl.style.color = '#e74c3c'; }
      if (btnEl) {
        btnEl.disabled = false; btnEl.style.opacity = '1';
        btnEl.style.boxShadow = '0 0 20px rgba(231,76,60,0.7)';
        setTimeout(() => { btnEl.style.boxShadow = 'none'; }, 1500);
      }
    });
  }
  _tier() {
    // Try known entity_id patterns first
    const ids = [
      "sensor.smartinghome_license_tier",
      "sensor.smarting_home_energy_management_license_tier",
      "sensor.smarting_home_license_tier",
    ];
    for (const id of ids) {
      const v = this._s(id);
      if (v && v !== "unknown" && v !== "unavailable") {
        if (this._lastTierEntity !== id) { console.log("[SH] License tier from:", id, "=", v); this._lastTierEntity = id; }
        return v.toUpperCase();
      }
    }
    // Fallback: search all states for *license_tier*
    if (this._hass?.states) {
      for (const [eid, st] of Object.entries(this._hass.states)) {
        if (eid.includes("license_tier") && st.state && st.state !== "unknown" && st.state !== "unavailable") {
          if (this._lastTierEntity !== eid) { console.log("[SH] License tier FOUND via search:", eid, "=", st.state); this._lastTierEntity = eid; }
          return st.state.toUpperCase();
        }
      }
    }
    if (!this._lastTierWarnLogged) { console.warn("[SH] No license_tier sensor found in HA states. Defaulting to FREE."); this._lastTierWarnLogged = true; }
    return "FREE";
  }

  async _loadSettings(retryCount = 0) {
    const MAX_RETRIES = 5;
    try {
      const r = await fetch('/local/smartinghome/settings.json?t=' + Date.now());
      if (r.ok) {
        this._settings = await r.json();
        this._settingsLoaded = true;
        // Restore day/night energy accumulators from settings immediately
        {
          const todayStr = new Date().toISOString().slice(0, 10);
          if (this._settings._accum_date === todayStr) {
            this._energyDayWs = this._settings._accum_day_ws || 0;
            this._energyNightWs = this._settings._accum_night_ws || 0;
          } else {
            this._energyDayWs = 0;
            this._energyNightWs = 0;
          }
          this._accumDate = todayStr;
          this._lastAccumTs = null; // reset so first interval doesn't produce huge delta
        }
        this._updateKeyStatus();
        // Restore model selections
        const gSel = this.shadowRoot.getElementById('sel-gemini-model');
        const aSel = this.shadowRoot.getElementById('sel-anthropic-model');
        if (gSel && this._settings.gemini_model) gSel.value = this._settings.gemini_model;
        if (aSel && this._settings.anthropic_model) aSel.value = this._settings.anthropic_model;
        // Show masked keys as placeholders & set dirty tracking
        const gInp = this.shadowRoot.getElementById('inp-gemini-key');
        const aInp = this.shadowRoot.getElementById('inp-anthropic-key');
        if (gInp) {
          if (this._settings.gemini_key_masked) gInp.placeholder = this._settings.gemini_key_masked;
          gInp.value = ''; this._geminiDirty = false;
          gInp.addEventListener('input', () => { this._geminiDirty = true; });
        }
        if (aInp) {
          if (this._settings.anthropic_key_masked) aInp.placeholder = this._settings.anthropic_key_masked;
          aInp.value = ''; this._anthropicDirty = false;
          aInp.addEventListener('input', () => { this._anthropicDirty = true; });
        }
        // Restore default provider
        const dpSel = this.shadowRoot.getElementById('sel-default-provider');
        if (dpSel && this._settings.default_ai_provider) dpSel.value = this._settings.default_ai_provider;
        // Restore tariff plan
        const tSel = this.shadowRoot.getElementById('sel-tariff-plan');
        if (tSel && this._settings.tariff_plan) tSel.value = this._settings.tariff_plan;
        // Restore cron settings
        this._loadCronSettings();
        // Show AI-powered HEMS advice if available
        this._updateHEMSFromAI();
        // Render AI logs
        this._renderAILogs();
        // Re-apply PV labels and all data after settings loaded
        if (this._hass) this._updateAll();
        this._renderWeatherForecast();
        // Restore Ecowitt settings
        const ecoChk = this.shadowRoot.getElementById('chk-ecowitt-enabled');
        if (ecoChk && this._settings.ecowitt_enabled !== undefined) ecoChk.checked = this._settings.ecowitt_enabled;
        if (this._settings.ecowitt_enabled) this._detectEcowittSensors();
        // Restore sub-meters settings
        const smChk = this.shadowRoot.getElementById('chk-submeters-enabled');
        if (smChk && this._settings.sub_meters_enabled !== undefined) smChk.checked = this._settings.sub_meters_enabled;
        const smCardChk = this.shadowRoot.getElementById('chk-submeters-in-card');
        if (smCardChk && this._settings.sub_meters_in_card !== undefined) smCardChk.checked = this._settings.sub_meters_in_card;
        this._renderSubMetersSettings();
      } else if (retryCount < MAX_RETRIES) {
        // Server returned error (e.g. not ready after restart) — retry
        const delay = 1000 * Math.pow(2, retryCount); // 1s, 2s, 4s, 8s, 16s
        console.warn(`[SH] settings.json fetch failed (HTTP ${r.status}), retry ${retryCount + 1}/${MAX_RETRIES} in ${delay}ms`);
        setTimeout(() => this._loadSettings(retryCount + 1), delay);
        return; // skip subscriptions setup until settings load
      }
    } catch(e) {
      // Network error or file not yet created — retry with backoff
      if (retryCount < MAX_RETRIES) {
        const delay = 1000 * Math.pow(2, retryCount);
        console.warn(`[SH] settings.json fetch error, retry ${retryCount + 1}/${MAX_RETRIES} in ${delay}ms`, e.message || e);
        setTimeout(() => this._loadSettings(retryCount + 1), delay);
        return;
      }
    }
    // Ensure subscriptions are active (centrally managed)
    this._ensureSubscriptions();
  }

  _savePanelSettings(updates) {
    Object.assign(this._settings, updates);
    if (this._hass) {
      this._hass.callService("smartinghome", "save_panel_settings", {
        settings: JSON.stringify(updates)
      });
    }
  }

  /* ── Custom Modal System ───────────────── */
  _showModal(html) {
    const overlay = this.shadowRoot.getElementById('sh-modal-overlay');
    const body = this.shadowRoot.getElementById('sh-modal-body');
    if (!overlay || !body) return;
    body.innerHTML = html;
    overlay.classList.add('active');
    const firstInput = body.querySelector('input[type="text"], input[type="number"]');
    if (firstInput) setTimeout(() => firstInput.focus(), 100);
  }
  _closeModal() {
    const overlay = this.shadowRoot.getElementById('sh-modal-overlay');
    if (overlay) overlay.classList.remove('active');
  }

  _editPvLabel(idx) {
    const current = (this._settings.pv_labels || {})[`pv${idx}`] || `PV${idx}`;
    const html = `
      <div class="sh-modal-title">✏️ Zmień etykietę PV${idx}</div>
      <div class="sh-modal-label">Nazwa stringa</div>
      <input class="sh-modal-input" type="text" id="sh-input-pv-label" value="${current.replace(/"/g, '&quot;')}" />
      <div class="sh-modal-actions">
        <button class="sh-modal-btn" onclick="this.getRootNode().host._closeModal()">Anuluj</button>
        <button class="sh-modal-btn primary" onclick="this.getRootNode().host._savePvLabel(${idx})">OK</button>
      </div>
    `;
    this._showModal(html);
    // Enter key support
    setTimeout(() => {
      const inp = this.shadowRoot.getElementById('sh-input-pv-label');
      if (inp) inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') this._savePvLabel(idx); });
    }, 120);
  }
  _savePvLabel(idx) {
    const inp = this.shadowRoot.getElementById('sh-input-pv-label');
    if (!inp) return;
    const newLabel = inp.value.trim();
    if (newLabel) {
      const labels = this._settings.pv_labels || {};
      labels[`pv${idx}`] = newLabel;
      this._savePanelSettings({ pv_labels: labels });
      // Update Overview tab label text span (preserves ⚙️ button)
      const el = this.shadowRoot.getElementById(`pv${idx}-label-text`);
      if (el) el.textContent = newLabel;
      // Update Energy tab label
      const enEl = this.shadowRoot.getElementById(`v-en-pv${idx}-label`);
      if (enEl) enEl.textContent = newLabel;
      // Also update sub-string displays if present
      this._renderSubstringBoxes();
    }
    this._closeModal();
  }

  /* ── PV String Configuration ────────────── */
  _getMaxStrings() {
    const tier = this._tier();
    return tier === "PRO" || tier === "ENTERPRISE" ? 10 : 4;
  }

  _getTotalStringCount() {
    const cfg = this._settings.pv_string_config || {};
    let count = 0;
    for (let i = 1; i <= 4; i++) {
      const sc = cfg[`pv${i}`];
      if (sc && sc.has_substrings && sc.substrings && sc.substrings.length > 1) {
        count += sc.substrings.length;
      } else {
        // Check if physical string has data
        const p = this._nm(`pv${i}_power`);
        if (p !== null || i <= 2) count++;
      }
    }
    return count;
  }

  _directionFactors() {
    // Returns factor for each direction at current hour (sinusoidal model)
    const hour = new Date().getHours() + new Date().getMinutes() / 60;
    const solarNoon = 12.5; // approximate solar noon in Poland
    // Sun angle from south (0 = south, ±180 = north)
    const sunAngle = (hour - solarNoon) * 15; // degrees per hour

    const dirs = {
      'S':  { peak: 12.5, spread: 5.0, base: 0.15 },
      'SE': { peak: 10.0, spread: 4.5, base: 0.12 },
      'SW': { peak: 15.0, spread: 4.5, base: 0.12 },
      'E':  { peak: 8.0,  spread: 4.0, base: 0.08 },
      'W':  { peak: 17.0, spread: 4.0, base: 0.08 },
      'NE': { peak: 7.5,  spread: 3.5, base: 0.05 },
      'NW': { peak: 17.5, spread: 3.5, base: 0.05 },
      'N':  { peak: 12.5, spread: 6.0, base: 0.03 },
    };

    // Sunrise/sunset approx 6-18 in spring, adjusted
    const sunrise = 6.0;
    const sunset = 18.5;

    const result = {};
    for (const [dir, cfg] of Object.entries(dirs)) {
      if (hour < sunrise || hour > sunset) {
        result[dir] = 0;
      } else {
        // Gaussian-like curve centered on peak hour
        const dist = hour - cfg.peak;
        const factor = Math.exp(-(dist * dist) / (2 * cfg.spread * cfg.spread));
        // Scale to max factor based on direction
        const maxFactors = { 'S': 1.0, 'SE': 0.85, 'SW': 0.85, 'E': 0.65, 'W': 0.65, 'NE': 0.45, 'NW': 0.45, 'N': 0.30 };
        result[dir] = Math.max(cfg.base, factor * maxFactors[dir]);
      }
    }
    return result;
  }

  _tiltFactor(tilt) {
    // Optimal tilt ~35° in Poland. Deviation reduces efficiency.
    const optimal = 35;
    const diff = Math.abs(tilt - optimal);
    return Math.max(0.5, 1.0 - (diff * diff) / 3000);
  }

  _calcSubstringRatios(stringIdx) {
    const cfg = this._settings.pv_string_config || {};
    const sc = cfg[`pv${stringIdx}`];
    if (!sc || !sc.has_substrings || !sc.substrings || sc.substrings.length < 2) return null;

    const dirFactors = this._directionFactors();
    const weights = sc.substrings.map(sub => {
      const dFactor = dirFactors[sub.direction] || 0.5;
      const tFactor = this._tiltFactor(sub.tilt || 35);
      return (sub.panel_count || 1) * (sub.panel_power || 400) * dFactor * tFactor;
    });
    const totalWeight = weights.reduce((a, b) => a + b, 0) || 1;
    return weights.map(w => w / totalWeight);
  }

  _openPvStringConfig(idx) {
    const cfg = this._settings.pv_string_config || {};
    const sc = cfg[`pv${idx}`] || { has_substrings: false, substrings: [{ direction: 'S', panel_count: 10, panel_power: 405, tilt: 35 }] };
    const pvLabel = (this._settings.pv_labels || {})[`pv${idx}`] || `PV${idx}`;
    const maxStrings = this._getMaxStrings();
    const tier = this._tier();

    // Store temp config for modal editing
    this._tempStringConfig = JSON.parse(JSON.stringify(sc));
    this._tempStringIdx = idx;

    this._renderStringConfigModal(idx, pvLabel, tier, maxStrings);
  }

  _renderStringConfigModal(idx, pvLabel, tier, maxStrings) {
    const sc = this._tempStringConfig;
    const directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
    const dirLabels = { 'N': 'Północ', 'NE': 'Pn-Wsch', 'E': 'Wschód', 'SE': 'Pd-Wsch', 'S': 'Południe', 'SW': 'Pd-Zach', 'W': 'Zachód', 'NW': 'Pn-Zach' };

    let subCardsHtml = '';
    if (sc.has_substrings && sc.substrings) {
      sc.substrings.forEach((sub, i) => {
        subCardsHtml += `
          <div class="substring-card">
            <div class="sc-header">
              <span class="sc-title">Podstring ${i + 1}</span>
              ${sc.substrings.length > 1 ? `<button class="sh-modal-btn danger" style="padding:4px 10px; font-size:10px" onclick="this.getRootNode().host._removeSubstring(${i})">✕ Usuń</button>` : ''}
            </div>
            <div class="sc-grid">
              <div class="sc-field">
                <label>🧭 Kierunek paneli</label>
                <select id="sh-ss-dir-${i}" onchange="this.getRootNode().host._updateTempSubstring(${i}, 'direction', this.value)">
                  ${directions.map(d => `<option value="${d}" ${sub.direction === d ? 'selected' : ''}>${d} — ${dirLabels[d]}</option>`).join('')}
                </select>
              </div>
              <div class="sc-field">
                <label>📐 Kąt nachylenia (°)</label>
                <input type="number" min="0" max="90" value="${sub.tilt || 35}" onchange="this.getRootNode().host._updateTempSubstring(${i}, 'tilt', parseFloat(this.value))" />
              </div>
              <div class="sc-field">
                <label>🔢 Ilość paneli</label>
                <input type="number" min="1" max="50" value="${sub.panel_count || 10}" onchange="this.getRootNode().host._updateTempSubstring(${i}, 'panel_count', parseInt(this.value))" />
              </div>
              <div class="sc-field">
                <label>⚡ Moc panela (Wp)</label>
                <input type="number" min="100" max="800" value="${sub.panel_power || 405}" onchange="this.getRootNode().host._updateTempSubstring(${i}, 'panel_power', parseInt(this.value))" />
              </div>
            </div>
          </div>
        `;
      });
    } else {
      // Single string — show basic config
      const sub = (sc.substrings && sc.substrings[0]) || { direction: 'S', panel_count: 10, panel_power: 405, tilt: 35 };
      subCardsHtml = `
        <div class="substring-card">
          <div class="sc-header"><span class="sc-title">Konfiguracja stringa</span></div>
          <div class="sc-grid">
            <div class="sc-field">
              <label>🧭 Kierunek paneli</label>
              <select id="sh-ss-dir-0" onchange="this.getRootNode().host._updateTempSubstring(0, 'direction', this.value)">
                ${directions.map(d => `<option value="${d}" ${sub.direction === d ? 'selected' : ''}>${d} — ${dirLabels[d]}</option>`).join('')}
              </select>
            </div>
            <div class="sc-field">
              <label>📐 Kąt nachylenia (°)</label>
              <input type="number" min="0" max="90" value="${sub.tilt || 35}" onchange="this.getRootNode().host._updateTempSubstring(0, 'tilt', parseFloat(this.value))" />
            </div>
            <div class="sc-field">
              <label>🔢 Ilość paneli</label>
              <input type="number" min="1" max="50" value="${sub.panel_count || 10}" onchange="this.getRootNode().host._updateTempSubstring(0, 'panel_count', parseInt(this.value))" />
            </div>
            <div class="sc-field">
              <label>⚡ Moc panela (Wp)</label>
              <input type="number" min="100" max="800" value="${sub.panel_power || 405}" onchange="this.getRootNode().host._updateTempSubstring(0, 'panel_power', parseInt(this.value))" />
            </div>
          </div>
        </div>
      `;
    }

    // Calculate live preview
    const previewHtml = this._renderSubstringPreview(idx);

    const html = `
      <div class="sh-modal-title">⚙️ Konfiguracja ${pvLabel}</div>
      <div style="display:flex; align-items:center; gap:10px; margin-bottom:14px">
        <label class="sh-toggle">
          <input type="checkbox" id="sh-has-substrings" ${sc.has_substrings ? 'checked' : ''}
            onchange="this.getRootNode().host._toggleSubstrings(this.checked)" />
          <span class="sh-toggle-slider"></span>
        </label>
        <span style="font-size:12px; color:#94a3b8">Ma podstringi (obwody równoległe)</span>
        <span style="font-size:10px; color:#64748b; margin-left:auto">${tier} — max ${maxStrings} stringów</span>
      </div>
      <div id="sh-substrings-list">
        ${subCardsHtml}
      </div>
      ${sc.has_substrings ? `<button class="sh-modal-btn" style="width:100%; margin-top:4px; font-size:11px; padding:8px"
        onclick="this.getRootNode().host._addSubstring()">+ Dodaj podstring</button>` : ''}
      <div id="sh-config-preview">${previewHtml}</div>
      <div class="sh-modal-actions">
        <button class="sh-modal-btn" onclick="this.getRootNode().host._closeModal()">Anuluj</button>
        <button class="sh-modal-btn primary" onclick="this.getRootNode().host._saveStringConfig()">💾 Zapisz</button>
      </div>
    `;
    this._showModal(html);
  }

  _renderSubstringPreview(idx) {
    const sc = this._tempStringConfig;
    if (!sc.substrings || sc.substrings.length === 0) return '';

    const parentPower = this._nm(`pv${idx}_power`) || 0;
    const parentVoltage = this._n(this._m(`pv${idx}_voltage`)) || 0;
    const parentCurrent = this._n(this._m(`pv${idx}_current`)) || 0;
    const pvTodayVal = this._nm('pv_today') || 0;
    const totalPv = this._nm('pv_power') || 1;
    const stringKwh = pvTodayVal * (parentPower / (totalPv || 1));

    if (!sc.has_substrings || sc.substrings.length < 2) {
      const sub = sc.substrings[0];
      const totalWp = (sub.panel_count || 1) * (sub.panel_power || 400);
      return `
        <div class="sh-modal-preview">
          <div class="prev-title">📊 Podgląd na żywo</div>
          <div class="prev-row">
            <span class="prev-name">Moc instalacji</span>
            <span class="prev-vals">${(totalWp / 1000).toFixed(2)} kWp (${sub.panel_count || 0} × ${sub.panel_power || 0} Wp)</span>
          </div>
          <div class="prev-row">
            <span class="prev-name">Aktualna moc</span>
            <span class="prev-vals" style="color:#f7b731; font-weight:700">${this._pw(parentPower)}</span>
          </div>
          <div class="prev-row">
            <span class="prev-name">Parametry</span>
            <span class="prev-vals">${parentVoltage.toFixed(1)} V · ${parentCurrent.toFixed(1)} A</span>
          </div>
          <div class="prev-row">
            <span class="prev-name">Produkcja dziś</span>
            <span class="prev-vals">${stringKwh.toFixed(1)} kWh</span>
          </div>
        </div>
      `;
    }

    // Multi sub-string preview with proportional calculations
    const dirFactors = this._directionFactors();
    const weights = sc.substrings.map(sub => {
      const dFactor = dirFactors[sub.direction] || 0.5;
      const tFactor = this._tiltFactor(sub.tilt || 35);
      return (sub.panel_count || 1) * (sub.panel_power || 400) * dFactor * tFactor;
    });
    const totalWeight = weights.reduce((a, b) => a + b, 0) || 1;
    const ratios = weights.map(w => w / totalWeight);

    let rowsHtml = sc.substrings.map((sub, i) => {
      const ratio = ratios[i];
      const subPower = parentPower * ratio;
      const subVoltage = parentVoltage; // voltage same on parallel connection
      const subCurrent = parentCurrent * ratio;
      const subKwh = stringKwh * ratio;
      const totalWp = (sub.panel_count || 1) * (sub.panel_power || 400);
      const dirLabels = { 'N': 'Północ', 'NE': 'Pn-Wsch', 'E': 'Wschód', 'SE': 'Pd-Wsch', 'S': 'Południe', 'SW': 'Pd-Zach', 'W': 'Zachód', 'NW': 'Pn-Zach' };
      return `
        <div class="prev-row" style="flex-wrap:wrap; padding:6px 0; border-bottom:1px solid rgba(255,255,255,0.04)">
          <span class="prev-name" style="width:100%; margin-bottom:3px">Podstring ${i + 1} — ${dirLabels[sub.direction] || sub.direction} ${sub.tilt}° (${totalWp / 1000} kWp)</span>
          <span class="prev-vals" style="width:100%">
            <span style="color:#f7b731; font-weight:700">${this._pw(subPower)}</span> ·
            ${subKwh.toFixed(1)} kWh ·
            ${subVoltage.toFixed(1)} V · ${subCurrent.toFixed(1)} A ·
            <span style="color:#00d4ff; font-weight:600">${(ratio * 100).toFixed(1)}%</span>
          </span>
        </div>
      `;
    }).join('');

    return `
      <div class="sh-modal-preview">
        <div class="prev-title">📊 Podgląd na żywo — podział proporcjonalny</div>
        ${rowsHtml}
        <div class="prev-row" style="padding-top:8px; font-weight:700">
          <span class="prev-name">Σ Suma</span>
          <span class="prev-vals" style="color:#f7b731">${this._pw(parentPower)} · ${stringKwh.toFixed(1)} kWh · ${parentVoltage.toFixed(1)} V · ${parentCurrent.toFixed(1)} A</span>
        </div>
      </div>
    `;
  }

  _toggleSubstrings(checked) {
    this._tempStringConfig.has_substrings = checked;
    if (checked && (!this._tempStringConfig.substrings || this._tempStringConfig.substrings.length < 2)) {
      // Add a second substring as default
      if (!this._tempStringConfig.substrings) this._tempStringConfig.substrings = [];
      if (this._tempStringConfig.substrings.length === 0) {
        this._tempStringConfig.substrings.push({ direction: 'S', panel_count: 6, panel_power: 405, tilt: 35 });
      }
      this._tempStringConfig.substrings.push({ direction: 'W', panel_count: 4, panel_power: 405, tilt: 35 });
    }
    const idx = this._tempStringIdx;
    const pvLabel = (this._settings.pv_labels || {})[`pv${idx}`] || `PV${idx}`;
    const tier = this._tier();
    this._renderStringConfigModal(idx, pvLabel, tier, this._getMaxStrings());
  }

  _addSubstring() {
    const maxStrings = this._getMaxStrings();
    const currentTotal = this._getTotalStringCount();
    if (currentTotal >= maxStrings) {
      // Show limit warning within modal
      const prev = this.shadowRoot.getElementById('sh-config-preview');
      if (prev) prev.innerHTML = `<div style="color:#e74c3c; font-size:11px; padding:8px; text-align:center">⚠️ Limit stringów (${maxStrings}) osiągnięty. Ulepsz do PRO aby dodać więcej.</div>`;
      return;
    }
    this._tempStringConfig.substrings.push({ direction: 'S', panel_count: 4, panel_power: 405, tilt: 35 });
    const idx = this._tempStringIdx;
    const pvLabel = (this._settings.pv_labels || {})[`pv${idx}`] || `PV${idx}`;
    const tier = this._tier();
    this._renderStringConfigModal(idx, pvLabel, tier, this._getMaxStrings());
  }

  _removeSubstring(subIdx) {
    this._tempStringConfig.substrings.splice(subIdx, 1);
    if (this._tempStringConfig.substrings.length < 2) {
      this._tempStringConfig.has_substrings = false;
    }
    const idx = this._tempStringIdx;
    const pvLabel = (this._settings.pv_labels || {})[`pv${idx}`] || `PV${idx}`;
    const tier = this._tier();
    this._renderStringConfigModal(idx, pvLabel, tier, this._getMaxStrings());
  }

  _updateTempSubstring(subIdx, field, value) {
    if (!this._tempStringConfig.substrings[subIdx]) return;
    this._tempStringConfig.substrings[subIdx][field] = value;
    // Update preview
    const prev = this.shadowRoot.getElementById('sh-config-preview');
    if (prev) prev.innerHTML = this._renderSubstringPreview(this._tempStringIdx);
  }

  _saveStringConfig() {
    const idx = this._tempStringIdx;
    const cfg = this._settings.pv_string_config || {};
    cfg[`pv${idx}`] = JSON.parse(JSON.stringify(this._tempStringConfig));
    this._savePanelSettings({ pv_string_config: cfg });
    this._closeModal();
    this._renderSubstringBoxes();
    if (this._hass) this._updateFlow();
  }

  _renderSubstringBoxes() {
    // Dynamically render sub-string boxes in the PV area
    const container = this.shadowRoot.querySelector('.pv-strings');
    if (!container) return;

    const cfg = this._settings.pv_string_config || {};
    const pvLabels = this._settings.pv_labels || {};

    // Clear existing dynamic boxes
    container.querySelectorAll('.pv-substring-box').forEach(el => el.remove());

    for (let i = 1; i <= 4; i++) {
      const sc = cfg[`pv${i}`];
      const mainBox = this.shadowRoot.getElementById(`pv${i}-box`);
      if (!mainBox) continue;

      if (sc && sc.has_substrings && sc.substrings && sc.substrings.length >= 2) {
        // Hide main box, show sub-strings instead
        mainBox.style.display = 'none';

        const ratios = this._calcSubstringRatios(i);
        const parentPower = this._nm(`pv${i}_power`) || 0;
        const parentVoltage = this._n(this._m(`pv${i}_voltage`)) || 0;
        const parentCurrent = this._n(this._m(`pv${i}_current`)) || 0;
        const pvTodayVal = this._nm('pv_today') || 0;
        const totalPv = this._nm('pv_power') || 1;
        const stringKwh = pvTodayVal * (parentPower / (totalPv || 1));
        const dirLabels = { 'N': 'Pn', 'NE': 'PnE', 'E': 'Wsch', 'SE': 'PdE', 'S': 'Pd', 'SW': 'PdZ', 'W': 'Zach', 'NW': 'PnZ' };
        const pvLabel = pvLabels[`pv${i}`] || `PV${i}`;

        sc.substrings.forEach((sub, si) => {
          const ratio = ratios ? ratios[si] : (1 / sc.substrings.length);
          const subPower = parentPower * ratio;
          const subVoltage = parentVoltage;
          const subCurrent = parentCurrent * ratio;
          const subKwh = stringKwh * ratio;

          const box = document.createElement('div');
          box.className = 'pv-string pv-substring-box';
          box.dataset.parentIdx = i;
          box.innerHTML = `
            <div class="pv-name" style="cursor:pointer" onclick="this.getRootNode().host._editPvLabel(${i})" title="Kliknij aby zmienić nazwę">${pvLabel} — ${dirLabels[sub.direction] || sub.direction}
              <span class="pv-config-btn" onclick="event.stopPropagation(); this.getRootNode().host._openPvStringConfig(${i})" title="Konfiguracja stringa">⚙️</span>
            </div>
            <div class="pv-val">${this._pw(subPower)}</div>
            <div class="pv-detail">${subVoltage.toFixed(1)} V · ${subCurrent.toFixed(1)} A</div>
            <div style="font-size:9px; color:#94a3b8; margin-top:2px">↑ ${subKwh.toFixed(1)} kWh</div>
          `;
          container.appendChild(box);
        });
      }
    }
  }

  _roiPeriod = "day";

  _switchRoiPeriod(period) {
    this._roiPeriod = period;
    ["day","week","month","year"].forEach(p => {
      const btn = this.shadowRoot.getElementById(`roi-period-${p}`);
      if (btn) btn.classList.toggle("active", p === period);
    });
    this._updateRoi();
  }

  _saveRoiInvestment(val) {
    const v = parseFloat(val) || 0;
    this._savePanelSettings({ roi_investment: v });
    this._updateRoi();
  }

  _n(entityId) {
    const state = this._hass?.states[entityId];
    if (!state || state.state === "unavailable" || state.state === "unknown") return null;
    const v = parseFloat(state.state);
    return isNaN(v) ? null : v;
  }

  _updateRoi() {
    if (!this._hass) return;
    const p = this._roiPeriod;
    const labels = { day: "dziś", week: "ten tydzień", month: "ten miesiąc", year: "ten rok" };
    this._setText("roi-period-label", `(${labels[p]})`);
    this._setText("roi-fin-label", `(${labels[p]})`);

    // Sensor mapping per period
    const suffixes = { day: "daily", week: "weekly", month: "monthly", year: "yearly" };
    const s = suffixes[p];

    // Energy
    const pvVal = p === "day" ? (this._n("sensor.today_s_pv_generation") ?? 0) : (this._n(`sensor.pv_${s}`) ?? 0);
    // GoodWe naming: grid_import = grid imports FROM you = YOUR EXPORT; grid_export = grid exports TO you = YOUR IMPORT
    const impVal = this._n(`sensor.grid_export_${s}`) ?? 0;  // YOUR import (grid exports TO you)
    const expVal = this._n(`sensor.grid_import_${s}`) ?? 0;  // YOUR export (grid imports FROM you)
    const selfUse = Math.max(0, pvVal - expVal);

    this._setText("roi-pv", `${pvVal.toFixed(1)} kWh`);
    this._setText("roi-import", `${impVal.toFixed(1)} kWh`);
    this._setText("roi-export", `${expVal.toFixed(1)} kWh`);
    this._setText("roi-selfuse", `${selfUse.toFixed(1)} kWh`);

    // Financial (G13 sensors)
    // GoodWe swap: g13_import_cost actually has export revenue, and vice versa
    const costVal = p === "day" ? (this._n("sensor.g13_export_revenue_today") ?? 0) : (this._n(`sensor.g13_export_revenue_${s}`) ?? 0);
    const revVal = p === "day" ? (this._n("sensor.g13_import_cost_today") ?? 0) : (this._n(`sensor.g13_import_cost_${s}`) ?? 0);
    const savVal = p === "day" ? (this._n("sensor.g13_self_consumption_savings_today") ?? 0) : (this._n(`sensor.g13_self_consumption_savings_${s}`) ?? 0);
    // Compute balance from corrected components (backend sensor has swapped sign)
    const balVal = revVal + savVal - costVal;

    this._setText("roi-cost", `${costVal.toFixed(2)} zł`);
    this._setText("roi-revenue", `${revVal.toFixed(2)} zł`);
    this._setText("roi-savings", `${savVal.toFixed(2)} zł`);
    this._setText("roi-balance", `${balVal >= 0 ? "+" : ""}${balVal.toFixed(2)} zł`);

    // Balance card colors
    const balCard = this.shadowRoot.getElementById("roi-balance-card");
    const balText = this.shadowRoot.getElementById("roi-balance");
    if (balCard) { balCard.style.borderColor = balVal >= 0 ? "#2ecc71" : "#e74c3c"; balCard.style.background = balVal >= 0 ? "rgba(46,204,113,0.1)" : "rgba(231,76,60,0.1)"; }
    if (balText) balText.style.color = balVal >= 0 ? "#2ecc71" : "#e74c3c";

    // Efficiency
    const autarky = (pvVal + impVal) > 0 ? Math.min(100, (pvVal / (pvVal + impVal)) * 100) : 0;
    const selfCons = pvVal > 0 ? Math.min(100, (selfUse / pvVal) * 100) : 0;
    this._setText("roi-autarky", `${autarky.toFixed(0)}%`);
    this._setText("roi-selfcons", `${selfCons.toFixed(0)}%`);
    const aBar = this.shadowRoot.getElementById("roi-autarky-bar");
    const scBar = this.shadowRoot.getElementById("roi-selfcons-bar");
    if (aBar) aBar.style.width = `${autarky}%`;
    if (scBar) scBar.style.width = `${selfCons}%`;

    // ROI calculation — 3 scenarios
    const invest = this._settings.roi_investment || 0;
    const invInput = this.shadowRoot.getElementById("roi-invest-input");
    if (invInput && !invInput.matches(":focus") && invest) invInput.value = invest;

    // Estimate yearly energy values from current period
    const multiplier = { day: 365, week: 52, month: 12, year: 1 };
    const yearlyPV = pvVal * multiplier[p];
    const yearlyImport = impVal * multiplier[p];
    const yearlyExport = expVal * multiplier[p];
    const yearlySelfUse = selfUse * multiplier[p];

    // Label
    const periodLabels = { day: "dzień", week: "tydzień", month: "miesiąc", year: "rok" };
    this._setText("roi-period-label", `Baza: ${periodLabels[p]} × ${multiplier[p]}`);

    // 3 scenarios
    const scenarios = this._winterScenarios();
    const keys = ["none", "basic", "optimal"];
    const colors = ["#e74c3c", "#f7b731", "#2ecc71"];
    const borders = ["rgba(231,76,60,0.2)", "rgba(247,183,49,0.2)", "rgba(46,204,113,0.3)"];
    const bgs = ["rgba(231,76,60,0.06)", "rgba(247,183,49,0.06)", "rgba(46,204,113,0.08)"];
    const badges = ["", "", "✅ REKOMENDOWANE"];

    const ct = this.shadowRoot.getElementById("roi-scenario-cards");
    // Total house consumption = self-use + import (what the house actually consumes)
    const totalConsumption = yearlySelfUse + yearlyImport;

    if (ct && yearlyPV > 0) {
      const results = keys.map((k, i) => {
        const s = scenarios[k];
        // Identical energy flows for all tariff variants — only prices differ
        const scSelfUse = yearlySelfUse;
        const scExport = yearlyExport;
        const scImport = yearlyImport;
        const scSelfConsPct = yearlyPV > 0 ? Math.round((yearlySelfUse / yearlyPV) * 100) : 0;

        const importCost = scImport * s.importPrice;
        const exportRev = scExport * s.exportPrice;
        const selfSavings = scSelfUse * s.importPrice;

        // yearlyBenefit = total annual value from PV (savings from self-consumption + export revenue)
        // This is the correct basis for ROI/payback: how much you save vs having NO PV at all
        const yearlyBenefit = selfSavings + exportRev;

        // yearlyBilans = net cash flow (export revenue minus import cost) — what hits your bank account
        const yearlyBilans = exportRev - importCost;

        const payback = invest > 0 && yearlyBenefit > 0 ? invest / yearlyBenefit : null;
        const profit25 = invest > 0 ? yearlyBenefit * 25 - invest : yearlyBenefit * 25;
        return { key: k, label: s.label, desc: s.desc, importCost, exportRev, selfSavings, yearlyBenefit, yearlyBilans, payback, profit25, importPrice: s.importPrice, exportPrice: s.exportPrice, scSelfUse, scExport, scImport, scSelfConsPct };
      });

      // Calculate savings vs worst scenario
      const worstBenefit = results[0].yearlyBenefit;

      ct.innerHTML = results.map((r, i) => {
        const paybackStr = r.payback ? `~${r.payback.toFixed(1)} lat` : (invest > 0 ? "∞ (strata)" : "— (podaj koszt)");
        const paybackPct = r.payback ? Math.min(100, (25 / r.payback) * (100 / 25)) : 0;
        const paybackColor = r.payback ? (r.payback <= 8 ? "#2ecc71" : r.payback <= 15 ? "#f7b731" : "#e74c3c") : "#e74c3c";
        const diffVsNone = i > 0 ? r.yearlyBenefit - worstBenefit : 0;
        return `<div style="background:${bgs[i]}; border:1px solid ${borders[i]}; border-radius:14px; padding:16px; position:relative; overflow:hidden">
          ${badges[i] ? `<div style="position:absolute; top:8px; right:8px; font-size:8px; color:${colors[i]}; font-weight:700; letter-spacing:0.5px">${badges[i]}</div>` : ""}
          <div style="font-size:13px; font-weight:700; color:${colors[i]}; margin-bottom:4px">${r.label}</div>
          <div style="font-size:9px; color:#64748b; margin-bottom:12px; line-height:1.4">${r.desc}</div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:4px; font-size:10px; margin-bottom:10px">
            <div style="color:#64748b">Import:</div><div style="color:#e74c3c; font-weight:600">${r.importPrice.toFixed(2)} zł/kWh</div>
            <div style="color:#64748b">Eksport:</div><div style="color:#2ecc71; font-weight:600">${r.exportPrice.toFixed(2)} zł/kWh</div>
          </div>
          <div style="background:rgba(255,255,255,0.03); border-radius:8px; padding:8px; margin-bottom:10px">
            <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">📊 Przepływy energii (identyczne)</div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:3px; font-size:10px">
              <div style="color:#94a3b8">🏠 Autokonsumpcja:</div><div style="color:#00d4ff; font-weight:600">${Math.round(r.scSelfUse)} kWh <span style="color:#64748b; font-weight:400">(${r.scSelfConsPct}%)</span></div>
              <div style="color:#94a3b8">↓ Import z sieci:</div><div style="color:#e74c3c; font-weight:600">${Math.round(r.scImport)} kWh</div>
              <div style="color:#94a3b8">↑ Eksport do sieci:</div><div style="color:#2ecc71; font-weight:600">${Math.round(r.scExport)} kWh</div>
            </div>
          </div>
          <div style="border-top:1px solid rgba(255,255,255,0.06); padding-top:8px">
            <div style="font-size:9px; color:#64748b">Oszczędności roczne (autokonsumpcja)</div>
            <div style="font-size:16px; font-weight:700; color:#00d4ff">${r.selfSavings.toFixed(0)} zł</div>
            <div style="font-size:9px; color:#64748b; margin-top:6px">Przychód z eksportu</div>
            <div style="font-size:16px; font-weight:700; color:#2ecc71">+${r.exportRev.toFixed(0)} zł</div>
            <div style="font-size:9px; color:#64748b; margin-top:6px">Koszt importu</div>
            <div style="font-size:16px; font-weight:700; color:#e74c3c">-${r.importCost.toFixed(0)} zł</div>
          </div>
          <div style="margin-top:10px; padding:10px; border-radius:10px; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.06)">
            <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">Korzyść roczna z PV</div>
            <div style="font-size:24px; font-weight:900; color:${r.yearlyBenefit >= 0 ? "#2ecc71" : "#e74c3c"}">${r.yearlyBenefit >= 0 ? "+" : ""}${r.yearlyBenefit.toFixed(0)} zł</div>
            <div style="font-size:9px; color:#94a3b8; margin-top:1px">oszczędność + przychód vs brak PV</div>
            ${i > 0 ? `<div style="font-size:10px; color:#2ecc71; margin-top:4px">+${diffVsNone.toFixed(0)} zł vs G11</div>` : `<div style="font-size:10px; color:#e74c3c; margin-top:4px">taryfa stała</div>`}
          </div>
          ${invest > 0 ? `
          <div style="margin-top:10px">
            <div style="display:flex; justify-content:space-between; font-size:9px; color:#64748b; margin-bottom:4px">
              <span>Zwrot inwestycji</span>
              <span style="color:${paybackColor}; font-weight:700">${paybackStr}</span>
            </div>
            <div style="background:rgba(255,255,255,0.08); border-radius:6px; height:8px; overflow:hidden">
              <div style="height:100%; width:${paybackPct}%; background:linear-gradient(90deg, ${paybackColor}, ${colors[i]}); border-radius:6px; transition:width 0.5s"></div>
            </div>
            <div style="display:flex; justify-content:space-between; font-size:9px; color:#64748b; margin-top:6px">
              <span>Zysk w 25 lat</span>
              <span style="color:${r.profit25 >= 0 ? "#2ecc71" : "#e74c3c"}; font-weight:700">${r.profit25 >= 0 ? "+" : ""}${Math.round(r.profit25).toLocaleString("pl-PL")} zł</span>
            </div>
          </div>` : ""}
        </div>`;
      }).join("");

      // Show total HEMS savings summary below cards
      const hemsSavings = results[2].yearlyBenefit - results[0].yearlyBenefit;
      if (hemsSavings > 0) {
        ct.innerHTML += `<div style="grid-column:1/-1; margin-top:12px; padding:12px; background:rgba(46,204,113,0.08); border:1px solid rgba(46,204,113,0.2); border-radius:10px; text-align:center">
          <div style="font-size:11px; color:#94a3b8">💰 Różnica cenowa: Dynamiczna RCE vs G11</div>
          <div style="font-size:28px; font-weight:900; color:#2ecc71; margin-top:4px">+${hemsSavings.toFixed(0)} zł/rok</div>
          <div style="font-size:10px; color:#64748b; margin-top:2px">Oszczędność dzięki taryfie dynamicznej z zarządzaniem HEMS vs stała cena G11</div>
        </div>`;
      }
    } else if (ct && yearlyPV === 0) {
      ct.innerHTML = '<div style="text-align:center; padding:20px; color:#64748b; font-size:11px">Brak danych PV — zmień okres na "Miesiąc" lub "Rok" aby zobaczyć porównanie.</div>';
    }

    // Summary table — all periods
    const periods = [
      { key: "d", suffix: "daily", label: "today", pvSensor: "sensor.today_s_pv_generation" },
      { key: "w", suffix: "weekly", pvSensor: "sensor.pv_weekly" },
      { key: "m", suffix: "monthly", pvSensor: "sensor.pv_monthly" },
      { key: "y", suffix: "yearly", pvSensor: "sensor.pv_yearly" },
    ];
    periods.forEach(({ key, suffix, pvSensor }) => {
      const pv = this._n(pvSensor) ?? 0;
      const imp = this._n(`sensor.grid_import_${suffix}`) ?? 0;
      const exp = this._n(`sensor.grid_export_${suffix}`) ?? 0;
      // Compute balance from corrected components (backend sensor has swapped sign)
      const cost_p = key === "d" ? (this._n("sensor.g13_export_revenue_today") ?? 0) : (this._n(`sensor.g13_export_revenue_${suffix}`) ?? 0);
      const rev_p = key === "d" ? (this._n("sensor.g13_import_cost_today") ?? 0) : (this._n(`sensor.g13_import_cost_${suffix}`) ?? 0);
      const sav_p = key === "d" ? (this._n("sensor.g13_self_consumption_savings_today") ?? 0) : (this._n(`sensor.g13_self_consumption_savings_${suffix}`) ?? 0);
      const bal = rev_p + sav_p - cost_p;
      this._setText(`roi-tbl-pv-${key}`, pv.toFixed(1));
      this._setText(`roi-tbl-imp-${key}`, imp.toFixed(1));
      this._setText(`roi-tbl-exp-${key}`, exp.toFixed(1));
      const balEl = this.shadowRoot.getElementById(`roi-tbl-bal-${key}`);
      if (balEl) { balEl.textContent = `${bal >= 0 ? "+" : ""}${bal.toFixed(2)}`; balEl.style.color = bal >= 0 ? "#2ecc71" : "#e74c3c"; }
    });
  }

  _updateG13Timeline() {
    const now = new Date();
    const hour = now.getHours();
    const minutes = now.getMinutes();
    const dow = now.getDay(); // 0=Sun, 6=Sat
    const month = now.getMonth(); // 0-based
    const isWeekend = (dow === 0 || dow === 6);
    const isSummer = (month >= 3 && month <= 8);
    const tariff = this._settings.tariff_plan || "G13";

    // Restore selector value
    const sel = this.shadowRoot.getElementById("sel-tariff-plan");
    if (sel && sel.value !== tariff) sel.value = tariff;

    // Zone logic per tariff
    const getZone = (h) => {
      if (tariff === "Dynamic") return "dynamic";
      if (tariff === "G11") return "flat";
      if (tariff === "G12") {
        if ((h >= 13 && h < 15) || h >= 22 || h < 6) return "off";
        return "peak";
      }
      if (tariff === "G12w") {
        if (isWeekend) return "off";
        if ((h >= 13 && h < 15) || h >= 22 || h < 6) return "off";
        return "peak";
      }
      // G13
      if (isWeekend) return "off";
      if (h >= 0 && h < 7) return "off";
      if (h >= 7 && h < 13) return "morning";
      if (isSummer) {
        if (h >= 13 && h < 19) return "off";
        if (h >= 19 && h < 22) return "peak";
        return "off";
      } else {
        if (h >= 13 && h < 16) return "off";
        if (h >= 16 && h < 21) return "peak";
        return "off";
      }
    };

    const colors = { off: "#2ecc71", morning: "#e67e22", peak: "#e74c3c", flat: "#3b82f6", dynamic: "#a855f7" };
    const labels = { off: "OFF-PEAK", morning: "PRZEDPOŁUDNIOWA", peak: "SZCZYT", flat: "STAŁA", dynamic: "DYNAMICZNA" };

    // Update title
    const titleEl = this.shadowRoot.getElementById("tariff-card-title");
    if (titleEl) titleEl.textContent = tariff === "Dynamic" ? "⚡ Taryfa Dynamiczna" : `⏰ Taryfa ${tariff}`;

    // Update timeline section label
    const tlLabel = this.shadowRoot.getElementById("g13-timeline-label");
    if (tlLabel) {
      const dayType = this.shadowRoot.getElementById("g13-day-type");
      const dayTypeHtml = dayType ? dayType.outerHTML : '';
      if (tariff === "Dynamic") {
        tlLabel.innerHTML = `Ceny godzinowe ENTSO-E ${dayTypeHtml}`;
      } else {
        tlLabel.innerHTML = `Harmonogram ${tariff} ${dayTypeHtml}`;
      }
    }

    // Render 24 segments
    const timeline = this.shadowRoot.getElementById("g13-timeline");
    if (timeline) {
      timeline.innerHTML = "";

      if (tariff === "Dynamic") {
        // Dynamic: try to read prices_today from HA sensor attributes
        const entsoeEntity = "sensor.entso_e_aktualna_cena_energii";
        let pricesToday = [];
        try {
          const stateObj = this._hass && this._hass.states && this._hass.states[entsoeEntity];
          if (stateObj && stateObj.attributes && stateObj.attributes.prices_today) {
            pricesToday = stateObj.attributes.prices_today;
          }
        } catch(e) {}

        if (pricesToday.length >= 24) {
          const prices = pricesToday.map(p => typeof p === 'object' ? (p.price || p.value || 0) : (parseFloat(p) || 0));
          const minP = Math.min(...prices);
          const maxP = Math.max(...prices);
          const range = maxP - minP || 1;

          for (let h = 0; h < 24; h++) {
            const p = prices[h] || 0;
            const ratio = (p - minP) / range; // 0=cheapest, 1=most expensive
            // Color gradient: green -> yellow -> orange -> red
            let color;
            if (ratio < 0.25) color = "#00e676";
            else if (ratio < 0.50) color = "#66bb6a";
            else if (ratio < 0.75) color = "#f7b731";
            else if (ratio < 0.90) color = "#e67e22";
            else color = "#e74c3c";

            const seg = document.createElement("div");
            seg.style.cssText = `flex:1; background:${color}; position:relative; transition:opacity 0.3s`;
            seg.title = `${String(h).padStart(2,'0')}:00 — ${p.toFixed(2)} zł/kWh`;
            if (h === hour) { seg.style.opacity = "1"; seg.style.boxShadow = "inset 0 0 0 1px #fff"; }
            else { seg.style.opacity = "0.7"; }
            if (h % 3 === 0) {
              const lbl = document.createElement("span");
              lbl.style.cssText = "position:absolute;left:1px;top:2px;font-size:6px;font-weight:700;color:#000;line-height:1";
              lbl.textContent = String(h).padStart(2,'0');
              seg.appendChild(lbl);
            }
            timeline.appendChild(seg);
          }
        } else {
          // Fallback: show current price as single block
          const allin = parseFloat(this._haState("sensor.entso_e_koszt_all_in_teraz")) || 0;
          for (let h = 0; h < 24; h++) {
            const seg = document.createElement("div");
            seg.style.cssText = `flex:1; background:#a855f7; position:relative; transition:opacity 0.3s; opacity:0.3`;
            seg.title = `${String(h).padStart(2,'0')}:00 — brak danych`;
            if (h === hour) {
              seg.style.opacity = "1";
              seg.style.boxShadow = "inset 0 0 0 1px #fff";
              seg.title = `${String(h).padStart(2,'0')}:00 — ${allin.toFixed(2)} zł/kWh (teraz)`;
            }
            timeline.appendChild(seg);
          }
        }
      } else {
        // Standard fixed-zone tariffs
        for (let h = 0; h < 24; h++) {
          const z = getZone(h);
          const seg = document.createElement("div");
          seg.style.cssText = `flex:1; background:${colors[z]}; position:relative; transition:opacity 0.3s`;
          seg.title = `${String(h).padStart(2,'0')}:00 — ${labels[z]}`;
          if (h === hour) { seg.style.opacity = "1"; seg.style.boxShadow = "inset 0 0 0 1px #fff"; }
          else { seg.style.opacity = "0.7"; }
          if (h % 3 === 0) {
            const lbl = document.createElement("span");
            lbl.style.cssText = "position:absolute;left:1px;top:2px;font-size:6px;font-weight:700;color:#000;line-height:1";
            lbl.textContent = String(h).padStart(2,'0');
            if (z === "peak") lbl.style.color = "#fff";
            seg.appendChild(lbl);
          }
          timeline.appendChild(seg);
        }
      }
    }

    // Now marker
    const marker = this.shadowRoot.getElementById("g13-now-marker");
    if (marker) {
      const pct = ((hour + minutes / 60) / 24) * 100;
      marker.style.left = `calc(${pct}% - 1px)`;
    }

    // Current zone badge
    const currentZone = tariff === "Dynamic" ? "dynamic" : getZone(hour);
    const badge = this.shadowRoot.getElementById("v-g13-zone-badge");
    if (badge) {
      if (tariff === "Dynamic") {
        const allin = parseFloat(this._haState("sensor.entso_e_koszt_all_in_teraz")) || 0;
        let dynLabel, dynColor;
        if (allin <= 0.10) { dynLabel = "B. TANIO"; dynColor = "#00e676"; }
        else if (allin <= 0.30) { dynLabel = "TANIO"; dynColor = "#2ecc71"; }
        else if (allin <= 0.60) { dynLabel = "NORMALNIE"; dynColor = "#f7b731"; }
        else if (allin <= 0.90) { dynLabel = "DROGO"; dynColor = "#e67e22"; }
        else { dynLabel = "B. DROGO"; dynColor = "#e74c3c"; }
        badge.textContent = `${dynLabel} (${allin.toFixed(2)} zł)`;
        badge.style.background = dynColor;
        badge.style.color = allin > 0.90 ? "#fff" : "#000";
      } else {
        badge.textContent = labels[currentZone];
        badge.style.background = colors[currentZone];
        badge.style.color = (currentZone === "peak") ? "#fff" : "#000";
      }
    }

    // Weekend badge
    const wkBadge = this.shadowRoot.getElementById("g13-weekend-badge");
    if (wkBadge) {
      const showWk = isWeekend && (tariff === "G13" || tariff === "G12w");
      wkBadge.style.display = showWk ? "inline" : "none";
    }

    // Day type label
    const dayType = this.shadowRoot.getElementById("g13-day-type");
    if (dayType) {
      if (tariff === "Dynamic") dayType.textContent = "(cena zmienia się co godzinę)";
      else if (tariff === "G11") dayType.textContent = "(stała cena — brak stref)";
      else if (isWeekend && (tariff === "G13" || tariff === "G12w")) dayType.textContent = "(weekend — cały dzień off-peak)";
      else dayType.textContent = "(dzień roboczy)";
    }

    // Season
    const seasonEl = this.shadowRoot.getElementById("v-g13-season");
    if (seasonEl) {
      if (tariff === "Dynamic") {
        seasonEl.textContent = "⚡ Cena godzinowa ENTSO-E";
        seasonEl.style.color = "#a855f7";
        seasonEl.parentElement.style.display = "";
      } else if (tariff === "G13") {
        seasonEl.textContent = isSummer ? "☀️ Lato (kwi–wrz)" : "❄️ Zima (paź–mar)";
        seasonEl.style.color = isSummer ? "#f7b731" : "#00d4ff";
        seasonEl.parentElement.style.display = "";
      } else {
        seasonEl.parentElement.style.display = "none";
      }
    }
  }

  _saveTariffPlan() {
    const sel = this.shadowRoot.getElementById("sel-tariff-plan");
    if (!sel) return;
    this._settings.tariff_plan = sel.value;
    this._savePanelSettings({ tariff_plan: sel.value });
    this._updateG13Timeline();
  }

  /** Shared tariff helper — returns zone info for any supported tariff */
  _getTariffInfo() {
    const tariff = this._settings.tariff_plan || "G13";
    const now = new Date();
    const h = now.getHours(), dow = now.getDay(), m = now.getMonth();
    const isWeekend = (dow === 0 || dow === 6);
    const isSummer = (m >= 3 && m <= 8);

    let zone, zoneName, zoneColor, price;

    if (tariff === "Dynamic") {
      // Read live ENTSO-E all-in price from HA state
      const allinNow = parseFloat(this._haState("sensor.entso_e_koszt_all_in_teraz")) || 0;
      price = allinNow.toFixed(2);
      if (allinNow <= 0.10) { zone = "very_cheap"; zoneName = "B. TANIO"; zoneColor = "#00e676"; }
      else if (allinNow <= 0.30) { zone = "cheap"; zoneName = "TANIO"; zoneColor = "#2ecc71"; }
      else if (allinNow <= 0.60) { zone = "normal"; zoneName = "NORMALNIE"; zoneColor = "#f7b731"; }
      else if (allinNow <= 0.90) { zone = "expensive"; zoneName = "DROGO"; zoneColor = "#e67e22"; }
      else { zone = "very_expensive"; zoneName = "B. DROGO"; zoneColor = "#e74c3c"; }
    } else if (tariff === "G11") {
      zone = "flat"; zoneName = "STAŁA"; zoneColor = "#3b82f6"; price = "0.87";
    } else if (tariff === "G12") {
      if ((h >= 13 && h < 15) || h >= 22 || h < 6) {
        zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = "0.55";
      } else {
        zone = "peak"; zoneName = "SZCZYT"; zoneColor = "#e74c3c"; price = "1.10";
      }
    } else if (tariff === "G12w") {
      if (isWeekend || (h >= 13 && h < 15) || h >= 22 || h < 6) {
        zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = "0.55";
      } else {
        zone = "peak"; zoneName = "SZCZYT"; zoneColor = "#e74c3c"; price = "1.10";
      }
    } else {
      // G13
      if (isWeekend) {
        zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = "0.63";
      } else if (h >= 7 && h < 13) {
        zone = "morning"; zoneName = "PORANNY"; zoneColor = "#e67e22"; price = "0.91";
      } else if (isSummer) {
        if (h >= 19 && h < 22) { zone = "peak"; zoneName = "SZCZYT"; zoneColor = "#e74c3c"; price = "1.50"; }
        else { zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = "0.63"; }
      } else {
        if (h >= 16 && h < 21) { zone = "peak"; zoneName = "SZCZYT"; zoneColor = "#e74c3c"; price = "1.50"; }
        else { zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = "0.63"; }
      }
    }

    return { tariff, zone, zoneName, zoneColor, price, isWeekend, isSummer, hour: h };
  }

  /** Average tariff price for savings calculations */
  _getTariffAvgPrice() {
    const t = this._settings.tariff_plan || "G13";
    if (t === "Dynamic") return parseFloat(this._haState("sensor.entso_e_srednia_dzisiaj")) || 0.50;
    if (t === "G11") return 0.87;
    if (t === "G12" || t === "G12w") return 0.82;
    return 0.87; // G13 weighted avg
  }

  _saveApiKeys() {
    const geminiModel = this.shadowRoot.getElementById("sel-gemini-model")?.value || "gemini-2.5-flash";
    const anthropicModel = this.shadowRoot.getElementById("sel-anthropic-model")?.value || "claude-sonnet-4-6";
    const defaultProvider = this.shadowRoot.getElementById("sel-default-provider")?.value || "gemini";
    if (this._hass) {
      // Save models + provider only (keys are managed via HA integration options)
      this._hass.callService("smartinghome", "save_settings", {
        gemini_model: geminiModel,
        anthropic_model: anthropicModel,
        default_ai_provider: defaultProvider,
      });
      const st = this.shadowRoot.getElementById("v-save-status");
      if (st) { st.textContent = '✅ Ustawienia AI zapisane!'; setTimeout(() => { st.textContent = ''; }, 4000); }
    }
  }

  _saveCronSettings() {
    const updates = {
      cron_hems_enabled: this.shadowRoot.getElementById("chk-cron-hems")?.checked ?? true,
      cron_hems_interval: parseInt(this.shadowRoot.getElementById("sel-cron-hems")?.value || "30"),
      cron_report_enabled: this.shadowRoot.getElementById("chk-cron-report")?.checked ?? true,
      cron_report_interval: parseInt(this.shadowRoot.getElementById("sel-cron-report")?.value || "360"),
      cron_anomaly_enabled: this.shadowRoot.getElementById("chk-cron-anomaly")?.checked ?? true,
      cron_anomaly_interval: parseInt(this.shadowRoot.getElementById("sel-cron-anomaly")?.value || "60"),
      cron_autopilot_enabled: this.shadowRoot.getElementById("chk-cron-autopilot")?.checked ?? true,
      cron_autopilot_interval: parseInt(this.shadowRoot.getElementById("sel-cron-autopilot")?.value || "10"),
    };
    this._savePanelSettings(updates);
    const st = this.shadowRoot.getElementById("v-cron-save-status");
    if (st) { st.textContent = "\u2705 Harmonogram zapisany! Restart HA wymagany."; setTimeout(() => { st.textContent = ""; }, 6000); }
  }

  _renderAILogs() {
    const el = this.shadowRoot.getElementById("ai-logs-body");
    if (!el) return;
    const logs = this._settings.ai_logs || [];
    if (logs.length === 0) {
      el.innerHTML = '<div style="text-align:center; color:#64748b; padding:20px; font-size:12px">Brak logów AI. Logi pojawią się po pierwszym uruchomieniu AI Cron.</div>';
      return;
    }
    const statusMap = { ok: ['✅', '#2ecc71'], truncated: ['⚠️', '#f39c12'], error: ['❌', '#e74c3c'] };
    const jobMap = { hems: '💡 HEMS', report: '📊 Raport', anomaly: '🔍 Anomalie' };
    const rows = logs.slice().reverse().map(l => {
      const [ico, clr] = statusMap[l.status] || ['❓', '#64748b'];
      const jobLabel = jobMap[l.job] || l.job;
      return `<tr style="border-bottom:1px solid rgba(255,255,255,0.04)">
        <td style="padding:6px 8px; font-size:10px; color:#94a3b8">${l.date} ${l.time}</td>
        <td style="padding:6px 8px; font-size:11px">${jobLabel}</td>
        <td style="padding:6px 8px; font-size:11px; color:#00d4ff">${l.provider}</td>
        <td style="padding:6px 8px; font-size:11px; text-align:right">${l.chars}</td>
        <td style="padding:6px 8px; font-size:11px; color:${clr}">${ico} ${l.status}${l.error ? ' — ' + l.error.substring(0, 60) : ''}</td>
      </tr>`;
    }).join('');
    el.innerHTML = `<table style="width:100%; border-collapse:collapse">
      <thead><tr style="border-bottom:1px solid rgba(255,255,255,0.1)">
        <th style="text-align:left; padding:6px 8px; font-size:9px; color:#64748b; text-transform:uppercase">Data</th>
        <th style="text-align:left; padding:6px 8px; font-size:9px; color:#64748b; text-transform:uppercase">Typ</th>
        <th style="text-align:left; padding:6px 8px; font-size:9px; color:#64748b; text-transform:uppercase">Dostawca</th>
        <th style="text-align:right; padding:6px 8px; font-size:9px; color:#64748b; text-transform:uppercase">Znaki</th>
        <th style="text-align:left; padding:6px 8px; font-size:9px; color:#64748b; text-transform:uppercase">Status</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
    // Update count
    const cnt = this.shadowRoot.getElementById("ai-logs-count");
    if (cnt) cnt.textContent = `${logs.length} wpisów`;
  }

  _clearAILogs() {
    this._settings.ai_logs = [];
    this._savePanelSettings({ ai_logs: [] });
    this._renderAILogs();
    const st = this.shadowRoot.getElementById("ai-logs-status");
    if (st) { st.textContent = "🗑️ Logi wyczyszczone!"; setTimeout(() => { st.textContent = ""; }, 3000); }
  }

  _renderWeatherForecast() {
    const el = this.shadowRoot.getElementById("weather-forecast-strip");
    if (!el || !this._hass?.states) return;
    const conditionEmoji = (txt) => {
      if (!txt) return '❓';
      const t = txt.toLowerCase();
      if (t.includes('słonecz') || t.includes('bezchmurn') || t.includes('jasn')) return '☀️';
      if (t.includes('przejaśn') || t.includes('częściowo')) return '⛅';
      if (t.includes('zachmurz') || t.includes('chmur') || t.includes('pochmurn')) return '🌥️';
      if (t.includes('deszcz') || t.includes('opady')) return '🌧️';
      if (t.includes('śnieg') || t.includes('mróz')) return '❄️';
      if (t.includes('burz') || t.includes('grzmot')) return '⛈️';
      if (t.includes('mgł') || t.includes('zamglen')) return '🌫️';
      return '🌤️';
    };
    const dayNames = ['Dziś', 'Jutro'];
    const now = new Date();
    for (let i = 2; i < 5; i++) {
      const d = new Date(now); d.setDate(d.getDate() + i);
      dayNames.push(d.toLocaleDateString('pl-PL', { weekday: 'short' }));
    }
    const s = (id) => this._hass.states[id]?.state;
    const cards = [];
    for (let i = 0; i < 5; i++) {
      const sun = s(`sensor.dom_godziny_sloneczne_dzien_${i}`);
      const uv = s(`sensor.dom_indeks_uv_dzien_${i}`);
      const temp = s(`sensor.dom_maksymalna_temperatura_realfeel_dzien_${i}`);
      const cond = s(`sensor.dom_warunki_pogodowe_dzien_${i}`);
      const wind = s(`sensor.dom_predkosc_wiatru_dzien_${i}`);
      if (!sun && !temp) continue;
      const emoji = conditionEmoji(cond);
      const uvColor = parseInt(uv) >= 6 ? '#e74c3c' : parseInt(uv) >= 3 ? '#f39c12' : '#2ecc71';
      const sunColor = parseFloat(sun) >= 5 ? '#f7b731' : parseFloat(sun) >= 2 ? '#f39c12' : '#e74c3c';
      cards.push(`<div style="flex:1; min-width:100px; padding:10px 8px; text-align:center; background:rgba(255,255,255,0.03); border-radius:10px; border:1px solid rgba(255,255,255,0.06)${i === 0 ? '; border-color:rgba(0,212,255,0.2); background:rgba(0,212,255,0.05)' : ''}">
        <div style="font-size:10px; font-weight:700; color:${i === 0 ? '#00d4ff' : '#94a3b8'}; text-transform:uppercase; letter-spacing:0.5px">${dayNames[i]}</div>
        <div style="font-size:24px; margin:4px 0">${emoji}</div>
        <div style="font-size:16px; font-weight:800; color:#fff">${temp ? Math.round(parseFloat(temp)) + '°' : '—'}</div>
        <div style="font-size:9px; color:#94a3b8; margin-top:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:100px">${cond || '—'}</div>
        <div style="margin-top:6px; display:flex; justify-content:center; gap:8px">
          <div style="font-size:10px"><span style="color:${sunColor}; font-weight:700">${sun || '—'}h</span> <span style="color:#64748b">☀️</span></div>
        </div>
        <div style="display:flex; justify-content:center; gap:8px; margin-top:2px">
          <div style="font-size:10px"><span style="color:${uvColor}; font-weight:600">UV ${uv || '—'}</span></div>
          ${wind ? `<div style="font-size:10px; color:#64748b">💨 ${Math.round(parseFloat(wind))}</div>` : ''}
        </div>
      </div>`);
    }
    if (cards.length === 0) {
      el.innerHTML = '<div style="text-align:center; color:#64748b; padding:12px; font-size:11px">Brak danych AccuWeather. Zainstaluj integrację AccuWeather w HA.</div>';
      return;
    }
    el.innerHTML = cards.join('');
  }

  _updateEcowittCard() {
    const el = this.shadowRoot.getElementById("ecowitt-weather-card");
    if (!el) return;
    const enabled = this._settings.ecowitt_enabled;
    el.style.display = enabled ? '' : 'none';
    if (!enabled || !this._hass?.states) {
      const pvSidebar = this.shadowRoot.getElementById('pv-eco-sidebar');
      if (pvSidebar) pvSidebar.style.display = 'none';
      return;
    }

    const s = (id) => {
      const st = this._hass.states[id];
      return (st && st.state !== 'unknown' && st.state !== 'unavailable') ? st.state : null;
    };
    const n = (id) => { const v = parseFloat(s(id)); return isNaN(v) ? null : v; };

    // Read Ecowitt sensors directly from HA states
    const temp = n('sensor.ecowitt_outdoor_temp_9747');
    const feelsLike = n('sensor.ecowitt_feels_like_temp_ch3');
    const humidity = n('sensor.ecowitt_outdoor_humidity_9747');
    const dewpoint = n('sensor.ecowitt_dewpoint_9747');
    const wind = n('sensor.ecowitt_wind_speed_9747');
    const gust = n('sensor.ecowitt_wind_gust_9747');
    const windDir = n('sensor.ecowitt_wind_direction_9747');
    const rainRate = n('sensor.ecowitt_rain_rate_9747');
    const dailyRain = n('sensor.ecowitt_daily_rain_9747');
    const solar = n('sensor.ecowitt_solar_radiation_9747');
    const lux = n('sensor.ecowitt_solar_lux_9747');
    const uv = n('sensor.ecowitt_uv_index_9747');
    const pressure = n('sensor.ecowitt_pressure_relative');

    const windDirLabel = (deg) => {
      if (deg === null) return '';
      const dirs = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];
      return dirs[Math.round(deg / 22.5) % 16];
    };
    const uvLabel = (v) => {
      if (v === null) return '—';
      if (v <= 2) return `${v} (niski)`;
      if (v <= 5) return `${v} (umiarkowany)`;
      if (v <= 7) return `${v} (wysoki)`;
      if (v <= 10) return `${v} (bardzo wysoki)`;
      return `${v} (ekstremalny)`;
    };
    const uvColor = (v) => {
      if (v === null) return '#64748b';
      if (v <= 2) return '#2ecc71';
      if (v <= 5) return '#f7b731';
      if (v <= 7) return '#f39c12';
      return '#e74c3c';
    };

    const body = this.shadowRoot.getElementById('ecowitt-body');
    if (!body) return;

    body.innerHTML = `
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px">
        <div style="background:rgba(255,255,255,0.03); border-radius:10px; padding:12px; border:1px solid rgba(255,255,255,0.06)">
          <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">🌡️ Temperatura</div>
          <div style="font-size:24px; font-weight:900; color:#fff; margin-top:4px">${temp !== null ? temp.toFixed(1) + '°C' : '—'}</div>
          <div style="font-size:10px; color:#94a3b8; margin-top:2px">Odczuwalna: ${feelsLike !== null ? feelsLike.toFixed(1) + '°C' : '—'}</div>
        </div>
        <div style="background:rgba(255,255,255,0.03); border-radius:10px; padding:12px; border:1px solid rgba(255,255,255,0.06)">
          <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">💧 Wilgotność</div>
          <div style="font-size:24px; font-weight:900; color:#3498db; margin-top:4px">${humidity !== null ? humidity + '%' : '—'}</div>
          <div style="font-size:10px; color:#94a3b8; margin-top:2px">Punkt rosy: ${dewpoint !== null ? dewpoint.toFixed(1) + '°C' : '—'}</div>
        </div>
        <div style="background:rgba(255,255,255,0.03); border-radius:10px; padding:12px; border:1px solid rgba(255,255,255,0.06)">
          <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">💨 Wiatr</div>
          <div style="font-size:20px; font-weight:800; color:#00d4ff; margin-top:4px">${wind !== null ? wind.toFixed(1) + ' km/h' + ' <span style="font-size:12px; color:#64748b">(' + (wind / 3.6).toFixed(1) + ' m/s)</span>' : '—'}</div>
          <div style="font-size:10px; color:#94a3b8; margin-top:2px">Porywy: ${gust !== null ? gust.toFixed(1) + ' km/h (' + (gust / 3.6).toFixed(1) + ' m/s)' : '—'} | ${windDir !== null ? windDir + '° ' + windDirLabel(windDir) : '—'}</div>
        </div>
        <div style="background:rgba(255,255,255,0.03); border-radius:10px; padding:12px; border:1px solid rgba(255,255,255,0.06)">
          <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">🌧️ Opady</div>
          <div style="font-size:20px; font-weight:800; color:${rainRate > 0 ? '#3498db' : '#2ecc71'}; margin-top:4px">${rainRate !== null ? rainRate.toFixed(1) + ' mm/h' : '—'}</div>
          <div style="font-size:10px; color:#94a3b8; margin-top:2px">Dziennie: ${dailyRain !== null ? dailyRain.toFixed(1) + ' mm' : '—'}</div>
        </div>
        <div style="background:rgba(255,255,255,0.03); border-radius:10px; padding:12px; border:1px solid rgba(255,255,255,0.06)">
          <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">☀️ Promieniowanie</div>
          <div style="font-size:20px; font-weight:800; color:#f7b731; margin-top:4px">${solar !== null ? solar.toFixed(0) + ' W/m²' : '—'}</div>
          <div style="font-size:10px; color:#94a3b8; margin-top:2px">${lux !== null ? Math.round(lux).toLocaleString('pl-PL') + ' lx' : '—'}</div>
        </div>
        <div style="background:rgba(255,255,255,0.03); border-radius:10px; padding:12px; border:1px solid rgba(255,255,255,0.06)">
          <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">🔆 UV & Ciśnienie</div>
          <div style="font-size:16px; font-weight:800; color:${uvColor(uv)}; margin-top:4px">UV ${uvLabel(uv)}</div>
          <div style="font-size:10px; color:#94a3b8; margin-top:2px">🌡️ ${pressure !== null ? pressure.toFixed(1) + ' hPa' : '—'}</div>
        </div>
      </div>
    `;

    // Update PV sidebar with weather data affecting production
    const pvSidebar = this.shadowRoot.getElementById('pv-eco-sidebar');
    if (pvSidebar) {
      pvSidebar.style.display = '';
      this._setText('pv-eco-solar', solar !== null ? `${solar.toFixed(0)} W/m²` : '—');
      const uvEl = this.shadowRoot.getElementById('pv-eco-uv');
      if (uvEl) {
        uvEl.textContent = uv !== null ? `${uv}` : '—';
        uvEl.style.color = uv === null ? '#64748b' : uv <= 2 ? '#2ecc71' : uv <= 5 ? '#f7b731' : '#e74c3c';
      }
      this._setText('pv-eco-temp', temp !== null ? `${temp.toFixed(1)}°C` : '—');
      this._setText('pv-eco-wind', wind !== null ? `${wind.toFixed(1)} km/h (${(wind / 3.6).toFixed(1)} m/s)` : '—');
    }
  }

  _saveEcowittSettings() {
    const chk = this.shadowRoot.getElementById('chk-ecowitt-enabled');
    if (!chk) return;
    const enabled = chk.checked;
    this._settings.ecowitt_enabled = enabled;
    this._savePanelSettings({ ecowitt_enabled: enabled });
    // Also sync to HA config entry so coordinator picks it up after restart
    if (this._hass) {
      this._hass.callService("smartinghome", "sync_ecowitt_state", { enabled });
    }
    // Update ecowitt detection status
    this._detectEcowittSensors();
    // Refresh card visibility
    this._updateEcowittCard();
    const st = this.shadowRoot.getElementById('v-ecowitt-save-status');
    if (st) { st.textContent = enabled ? '✅ Ecowitt włączony!' : '❌ Ecowitt wyłączony'; setTimeout(() => { st.textContent = ''; }, 4000); }
  }

  /* ── Sub-Meters Management ─────────────── */

  _saveSubMetersSettings() {
    const chk = this.shadowRoot.getElementById('chk-submeters-enabled');
    if (!chk) return;
    const enabled = chk.checked;
    this._settings.sub_meters_enabled = enabled;
    const inCard = this.shadowRoot.getElementById('chk-submeters-in-card');
    const inCardEnabled = inCard ? inCard.checked : false;
    this._settings.sub_meters_in_card = inCardEnabled;
    const meters = this._settings.sub_meters || [];
    this._savePanelSettings({ sub_meters_enabled: enabled, sub_meters_in_card: inCardEnabled, sub_meters: meters });
    this._updateSubMeters();
    const st = this.shadowRoot.getElementById('v-submeters-save-status');
    if (st) {
      st.textContent = '✅ Podliczniki zapisane! (' + meters.length + ' szt.)';
      setTimeout(() => { st.textContent = ''; }, 4000);
    }
    console.log('[SmartingHOME] Sub-meters saved:', { enabled, count: meters.length });
  }

  _addSubMeter() {
    const meters = this._settings.sub_meters || [];
    if (meters.length >= 8) {
      this._showModal('<div class="sh-modal-title">⚠️ Limit</div><div style="color:#94a3b8; font-size:12px; margin:10px 0">Maksymalnie 8 podliczników.</div><div class="sh-modal-actions"><button class="sh-modal-btn primary" onclick="this.getRootNode().host._closeModal()">OK</button></div>');
      return;
    }
    this._showSubMeterModal(null);
  }

  _editSubMeter(id) {
    const meters = this._settings.sub_meters || [];
    const meter = meters.find(m => m.id === id);
    if (!meter) return;
    this._showSubMeterModal(meter);
  }

  _removeSubMeter(id) {
    const meters = this._settings.sub_meters || [];
    this._settings.sub_meters = meters.filter(m => m.id !== id);
    this._savePanelSettings({ sub_meters: this._settings.sub_meters });
    this._renderSubMetersSettings();
    this._updateSubMeters();
    this._updateSubMetersInCard();
    console.log('[SmartingHOME] Sub-meter removed:', id);
  }

  _showSubMeterModal(existing) {
    const isEdit = !!existing;
    const name = existing ? existing.name.replace(/"/g, '&quot;') : '';
    const icon = existing ? existing.icon : '⚡';
    const entityId = existing ? (existing.entity_id || '') : '';
    const energyEntityId = existing ? (existing.energy_entity_id || '') : '';
    const editId = existing ? existing.id : '';

    const html = `
      <div class="sh-modal-title">${isEdit ? '✏️ Edytuj' : '＋ Dodaj'} podlicznik</div>
      <div class="sh-modal-label">Ikona (emoji)</div>
      <input class="sh-modal-input" type="text" id="sh-sm-icon" value="${icon}" maxlength="4" style="width:60px; text-align:center; font-size:22px" />
      <div class="sh-modal-label" style="margin-top:10px">Nazwa punktu poboru</div>
      <input class="sh-modal-input" type="text" id="sh-sm-name" value="${name}" placeholder="np. Kuchnia, Klimatyzacja..." />
      <div class="sh-modal-label" style="margin-top:10px">Encja mocy (W) — sensor</div>
      <input class="sh-modal-input" type="text" id="sh-sm-entity" value="${entityId}" placeholder="sensor.kitchen_power" oninput="this.getRootNode().host._onSmEntityInput(this, 'sh-sm-entity-suggest', 'power')" autocomplete="off" />
      <div id="sh-sm-entity-suggest" class="sm-suggest-list" style="display:none"></div>
      <div class="sh-modal-label" style="margin-top:10px">Encja energii (kWh) — opcjonalnie</div>
      <input class="sh-modal-input" type="text" id="sh-sm-energy-entity" value="${energyEntityId}" placeholder="sensor.kitchen_energy_daily" oninput="this.getRootNode().host._onSmEntityInput(this, 'sh-sm-energy-suggest', 'energy')" autocomplete="off" />
      <div id="sh-sm-energy-suggest" class="sm-suggest-list" style="display:none"></div>
      <input type="hidden" id="sh-sm-edit-id" value="${editId}" />
      <div class="sh-modal-actions" style="margin-top:14px">
        <button class="sh-modal-btn" onclick="this.getRootNode().host._closeModal()">Anuluj</button>
        <button class="sh-modal-btn primary" onclick="this.getRootNode().host._saveSubMeterFromModal()">💾 Zapisz</button>
      </div>
    `;
    this._showModal(html);
  }

  _onSmEntityInput(inputEl, suggestId, type) {
    const suggestEl = this.shadowRoot.getElementById(suggestId);
    if (!suggestEl || !this._hass?.states) return;
    const val = inputEl.value.toLowerCase().trim();
    if (val.length < 2) { suggestEl.style.display = 'none'; return; }

    const allEntities = Object.keys(this._hass.states).filter(e => e.startsWith('sensor.'));
    let filtered;
    if (type === 'power') {
      filtered = allEntities.filter(e => {
        const lc = e.toLowerCase();
        return lc.includes(val) || lc.includes('power') || lc.includes('watt') || lc.includes('load') || lc.includes('moc');
      });
    } else {
      filtered = allEntities.filter(e => {
        const lc = e.toLowerCase();
        return lc.includes(val) || lc.includes('energy') || lc.includes('kwh') || lc.includes('consumption') || lc.includes('zuzycie');
      });
    }
    // Prioritize entities matching the user's input
    filtered = filtered.filter(e => e.toLowerCase().includes(val)).slice(0, 12);

    if (filtered.length === 0) { suggestEl.style.display = 'none'; return; }
    suggestEl.style.display = 'block';
    suggestEl.innerHTML = filtered.map(e => {
      const state = this._hass.states[e];
      const unit = state?.attributes?.unit_of_measurement || '';
      const friendly = state?.attributes?.friendly_name || e;
      return `<div class="sm-suggest-item" onclick="this.getRootNode().host._selectSmEntity('${inputEl.id}', '${e}', '${suggestId}')">${friendly} <span style="color:#64748b">(${state?.state || '—'} ${unit})</span></div>`;
    }).join('');
  }

  _selectSmEntity(inputId, entityId, suggestId) {
    const inp = this.shadowRoot.getElementById(inputId);
    const sug = this.shadowRoot.getElementById(suggestId);
    if (inp) inp.value = entityId;
    if (sug) sug.style.display = 'none';
  }

  _saveSubMeterFromModal() {
    const nameEl = this.shadowRoot.getElementById('sh-sm-name');
    const iconEl = this.shadowRoot.getElementById('sh-sm-icon');
    const entityEl = this.shadowRoot.getElementById('sh-sm-entity');
    const energyEl = this.shadowRoot.getElementById('sh-sm-energy-entity');
    const editIdEl = this.shadowRoot.getElementById('sh-sm-edit-id');
    if (!nameEl || !entityEl) return;

    const name = nameEl.value.trim();
    const icon = iconEl ? iconEl.value.trim() || '⚡' : '⚡';
    const entityId = entityEl.value.trim();
    const energyEntityId = energyEl ? energyEl.value.trim() : '';
    const editId = editIdEl ? editIdEl.value : '';

    if (!name || !entityId) {
      nameEl.style.borderColor = !name ? '#e74c3c' : '';
      entityEl.style.borderColor = !entityId ? '#e74c3c' : '';
      return;
    }

    const meters = this._settings.sub_meters || [];

    if (editId) {
      // Edit existing
      const idx = meters.findIndex(m => m.id === editId);
      if (idx >= 0) {
        meters[idx] = { ...meters[idx], name, icon, entity_id: entityId, energy_entity_id: energyEntityId };
      }
    } else {
      // Add new
      const newId = 'sm_' + Date.now();
      meters.push({ id: newId, name, icon, entity_id: entityId, energy_entity_id: energyEntityId });
    }

    this._settings.sub_meters = meters;
    // Auto-enable and persist immediately
    this._settings.sub_meters_enabled = true;
    const chk = this.shadowRoot.getElementById('chk-submeters-enabled');
    if (chk) chk.checked = true;
    this._savePanelSettings({ sub_meters_enabled: true, sub_meters: meters });
    this._closeModal();
    this._renderSubMetersSettings();
    this._updateSubMeters();
    this._updateSubMetersInCard();
    console.log('[SmartingHOME] Sub-meter saved from modal:', { name: meters[meters.length-1]?.name, count: meters.length });
  }

  _moveSubMeter(id, direction) {
    const meters = this._settings.sub_meters || [];
    const idx = meters.findIndex(m => m.id === id);
    if (idx < 0) return;
    const newIdx = idx + direction;
    if (newIdx < 0 || newIdx >= meters.length) return;
    [meters[idx], meters[newIdx]] = [meters[newIdx], meters[idx]];
    this._settings.sub_meters = meters;
    this._savePanelSettings({ sub_meters: meters });
    this._renderSubMetersSettings();
    this._updateSubMeters();
    this._updateSubMetersInCard();
  }

  _renderSubMetersSettings() {
    const container = this.shadowRoot.getElementById('submeters-settings-list');
    if (!container) return;
    const meters = this._settings.sub_meters || [];

    if (meters.length === 0) {
      container.innerHTML = '<div style="text-align:center; color:#64748b; font-size:11px; padding:16px">Brak podliczników. Kliknij „＋ Dodaj podlicznik" poniżej.</div>';
      return;
    }

    container.innerHTML = meters.map((m, i) => `
      <div class="sm-list-item">
        <div class="sm-list-icon">${m.icon || '⚡'}</div>
        <div class="sm-list-info">
          <div class="sm-list-name">${m.name}</div>
          <div class="sm-list-entity">${m.entity_id}${m.energy_entity_id ? ' · ' + m.energy_entity_id : ''}</div>
        </div>
        <div class="sm-list-actions">
          <button class="sm-list-btn" onclick="this.getRootNode().host._moveSubMeter('${m.id}', -1)" title="W górę" ${i === 0 ? 'disabled style="opacity:0.3"' : ''}>▲</button>
          <button class="sm-list-btn" onclick="this.getRootNode().host._moveSubMeter('${m.id}', 1)" title="W dół" ${i === meters.length - 1 ? 'disabled style="opacity:0.3"' : ''}>▼</button>
          <button class="sm-list-btn" onclick="this.getRootNode().host._editSubMeter('${m.id}')" title="Edytuj">✏️</button>
          <button class="sm-list-btn danger" onclick="this.getRootNode().host._removeSubMeter('${m.id}')" title="Usuń">🗑️</button>
        </div>
      </div>
    `).join('');
  }

  _updateSubMeters() {
    const section = this.shadowRoot.getElementById('submeters-section');
    const grid = this.shadowRoot.getElementById('submeters-grid');
    if (!section || !grid) return;

    // Check enabled: from settings OR from DOM checkbox as fallback
    let enabled = this._settings.sub_meters_enabled;
    if (enabled === undefined) {
      const chk = this.shadowRoot.getElementById('chk-submeters-enabled');
      enabled = chk ? chk.checked : false;
    }
    const meters = this._settings.sub_meters || [];

    if (!enabled || meters.length === 0) {
      section.style.display = 'none';
      return;
    }
    section.style.display = '';

    // Get total home load for proportion bars
    const totalLoadStr = this._hass?.states?.['sensor.load']?.state;
    const totalLoad = parseFloat(totalLoadStr) || 0;

    grid.innerHTML = meters.map(m => {
      const powerState = this._hass?.states?.[m.entity_id];
      const powerVal = powerState ? parseFloat(powerState.state) : NaN;
      const powerStr = isNaN(powerVal) ? '—' : Math.round(powerVal).toLocaleString('pl-PL');
      const unit = powerState?.attributes?.unit_of_measurement || 'W';

      let energyHtml = '';
      if (m.energy_entity_id) {
        const energyState = this._hass?.states?.[m.energy_entity_id];
        const energyVal = energyState ? parseFloat(energyState.state) : NaN;
        const energyUnit = energyState?.attributes?.unit_of_measurement || 'kWh';
        const energyStr = isNaN(energyVal) ? '—' : energyVal.toFixed(1);
        energyHtml = `<div class="submeter-energy">📊 ${energyStr} ${energyUnit}</div>`;
      }

      // Proportion bar
      let pct = 0;
      if (!isNaN(powerVal) && totalLoad > 0) {
        pct = Math.min(100, Math.round((Math.abs(powerVal) / totalLoad) * 100));
      }

      // Color based on power level
      let powerColor = '#fff';
      if (!isNaN(powerVal)) {
        if (powerVal > 2000) powerColor = '#e74c3c';
        else if (powerVal > 500) powerColor = '#f7b731';
        else if (powerVal > 0) powerColor = '#2ecc71';
        else powerColor = '#64748b';
      }

      return `
        <div class="submeter-card">
          <div class="submeter-icon">${m.icon || '⚡'}</div>
          <div class="submeter-name">${m.name}</div>
          <div class="submeter-power" style="color:${powerColor}">${powerStr} <span style="font-size:11px; font-weight:600">${unit}</span></div>
          ${energyHtml}
          <div class="submeter-bar-bg">
            <div class="submeter-bar-fill" style="width:${pct}%"></div>
          </div>
          <div style="font-size:8px; color:#475569; margin-top:3px">${pct}% zużycia domu</div>
        </div>
      `;
    }).join('');
  }

  _updateSubMetersInCard() {
    const container = this.shadowRoot.getElementById('submeters-in-card');
    if (!container) return;

    let enabled = this._settings.sub_meters_in_card;
    if (enabled === undefined) {
      const chk = this.shadowRoot.getElementById('chk-submeters-in-card');
      enabled = chk ? chk.checked : false;
    }
    const meters = this._settings.sub_meters || [];

    if (!enabled || meters.length === 0) {
      container.style.display = 'none';
      return;
    }
    container.style.display = '';

    container.innerHTML = meters.map(m => {
      const powerState = this._hass?.states?.[m.entity_id];
      const powerVal = powerState ? parseFloat(powerState.state) : NaN;
      const powerStr = isNaN(powerVal) ? '—' : Math.round(powerVal).toLocaleString('pl-PL');
      const unit = powerState?.attributes?.unit_of_measurement || 'W';

      let color = '#94a3b8';
      if (!isNaN(powerVal)) {
        if (powerVal > 2000) color = '#e74c3c';
        else if (powerVal > 500) color = '#f7b731';
        else if (powerVal > 0) color = '#2ecc71';
      }

      return `<div style="display:flex; align-items:center; justify-content:space-between; padding:3px 0; font-size:11px">
        <span style="color:#94a3b8">${m.icon || '⚡'} ${m.name}</span>
        <span style="color:${color}; font-weight:700">${powerStr} ${unit}</span>
      </div>`;
    }).join('');
  }

  _detectEcowittSensors() {
    const statusEl = this.shadowRoot.getElementById('ecowitt-detect-status');
    if (!statusEl || !this._hass?.states) return;
    const sensors = [
      'sensor.ecowitt_outdoor_temp_9747',
      'sensor.ecowitt_outdoor_humidity_9747',
      'sensor.ecowitt_wind_speed_9747',
      'sensor.ecowitt_solar_radiation_9747',
    ];
    const found = sensors.filter(s => this._hass.states[s] && this._hass.states[s].state !== 'unavailable');
    if (found.length >= 3) {
      statusEl.innerHTML = '✅ <strong>Wykryto stację WH90</strong> — ' + found.length + '/4 główne sensory aktywne';
      statusEl.style.color = '#2ecc71';
    } else if (found.length > 0) {
      statusEl.innerHTML = '⚠️ Częściowo wykryto — ' + found.length + '/4 sensory';
      statusEl.style.color = '#f39c12';
    } else {
      statusEl.innerHTML = '❌ Nie wykryto sensorów Ecowitt. Zainstaluj integrację <strong>Ecowitt Local</strong> w HA.';
      statusEl.style.color = '#e74c3c';
    }
  }

  /* ── Wind Power Tab ─────────────────────── */
  _windTurbinePresets = {
    small:  { label: '🌬️ Mała (1 kW)',   power_kw: 1, rotor_diameter: 1.8, cut_in: 2.5, rated_speed: 11, investment: 8000,  price_kwh: 0.87 },
    medium: { label: '💨 Średnia (3 kW)', power_kw: 3, rotor_diameter: 3.2, cut_in: 3.0, rated_speed: 12, investment: 25000, price_kwh: 0.87 },
    large:  { label: '🌪️ Duża (5 kW)',   power_kw: 5, rotor_diameter: 5.0, cut_in: 2.5, rated_speed: 13, investment: 45000, price_kwh: 0.87 },
  };
  _windTurbineDefaults = { power_kw: 3, rotor_diameter: 3.2, cut_in: 3.0, rated_speed: 12, investment: 25000, price_kwh: 0.87 };
  _windActivePreset = 'medium';

  _initWindTab() {
    if (this._windInitialized) return;
    this._windInitialized = true;
    if (!this._settings.wind_turbine) {
      this._applyWindPreset('medium', true);
    }
  }

  _loadWindData() {
    this._windLoading = true;
    const wt = this._settings.wind_turbine;
    if (!wt) {
      this._applyWindPreset('medium', true);
      this._windLoading = false;
      return;
    }
    const fields = [
      ['wind-turbine-power', wt.power_kw],
      ['wind-turbine-diameter', wt.rotor_diameter],
      ['wind-turbine-cutin', wt.cut_in],
      ['wind-turbine-rated', wt.rated_speed],
      ['wind-turbine-investment', wt.investment],
      ['wind-turbine-price', wt.price_kwh],
    ];
    fields.forEach(([id, val]) => {
      const el = this.shadowRoot.getElementById(id);
      if (el && val !== undefined) el.value = val;
    });
    this._windActivePreset = this._settings.wind_turbine_preset || 'custom';
    this._updateWindPresetButtons();
    this._recalcWindProfitability();
    // Keep _windLoading true longer than the 1500ms auto-save debounce to prevent overwrite
    setTimeout(() => { this._windLoading = false; }, 2500);
  }

  _saveWindData() {
    const g = (id) => parseFloat(this.shadowRoot.getElementById(id)?.value) || 0;
    const wt = {
      power_kw: g('wind-turbine-power'),
      rotor_diameter: g('wind-turbine-diameter'),
      cut_in: g('wind-turbine-cutin'),
      rated_speed: g('wind-turbine-rated'),
      investment: g('wind-turbine-investment'),
      price_kwh: g('wind-turbine-price'),
    };
    this._savePanelSettings({ wind_turbine: wt, wind_turbine_preset: this._windActivePreset });
    const st = this.shadowRoot.getElementById('wind-save-status');
    if (st) { st.textContent = '✅ Zapisano konfigurację turbiny!'; setTimeout(() => { st.textContent = ''; }, 4000); }
  }

  _applyWindPreset(key, silent = false) {
    const preset = this._windTurbinePresets[key];
    if (!preset) return;
    this._windActivePreset = key;
    const fields = [
      ['wind-turbine-power', preset.power_kw],
      ['wind-turbine-diameter', preset.rotor_diameter],
      ['wind-turbine-cutin', preset.cut_in],
      ['wind-turbine-rated', preset.rated_speed],
      ['wind-turbine-investment', preset.investment],
      ['wind-turbine-price', preset.price_kwh],
    ];
    fields.forEach(([id, val]) => {
      const el = this.shadowRoot.getElementById(id);
      if (el) el.value = val;
    });
    this._updateWindPresetButtons();
    this._recalcWindProfitability();
    if (!silent) {
      this._saveWindData();
      const st = this.shadowRoot.getElementById('wind-save-status');
      if (st) { st.textContent = '✅ Wczytano wariant: ' + preset.label; setTimeout(() => { st.textContent = ''; }, 4000); }
    }
  }

  _onWindFieldManualChange() {
    this._windActivePreset = 'custom';
    this._updateWindPresetButtons();
    this._recalcWindProfitability();
  }

  _updateWindPresetButtons() {
    ['small', 'medium', 'large'].forEach(k => {
      const btn = this.shadowRoot.getElementById('wind-preset-' + k);
      if (btn) {
        const isActive = this._windActivePreset === k;
        btn.style.background = isActive ? 'rgba(0,212,255,0.2)' : 'rgba(255,255,255,0.05)';
        btn.style.borderColor = isActive ? '#00d4ff' : 'rgba(255,255,255,0.12)';
        btn.style.color = isActive ? '#00d4ff' : '#94a3b8';
      }
    });
    const customBadge = this.shadowRoot.getElementById('wind-preset-custom-badge');
    if (customBadge) {
      customBadge.style.display = this._windActivePreset === 'custom' ? 'inline' : 'none';
    }
  }

  _getBeaufort(speed) {
    // speed in km/h
    if (speed < 1) return { scale: 0, name: 'Cisza', color: '#64748b', icon: '🍃' };
    if (speed < 6) return { scale: 1, name: 'Powiew', color: '#94a3b8', icon: '🍃' };
    if (speed < 12) return { scale: 2, name: 'Słaby wiatr', color: '#2ecc71', icon: '🌿' };
    if (speed < 20) return { scale: 3, name: 'Łagodny', color: '#27ae60', icon: '🌿' };
    if (speed < 29) return { scale: 4, name: 'Umiarkowany', color: '#f7b731', icon: '🌬️' };
    if (speed < 39) return { scale: 5, name: 'Dość silny', color: '#f39c12', icon: '🌬️' };
    if (speed < 50) return { scale: 6, name: 'Silny', color: '#e67e22', icon: '💨' };
    if (speed < 62) return { scale: 7, name: 'Bardzo silny', color: '#e74c3c', icon: '💨' };
    if (speed < 75) return { scale: 8, name: 'Sztormowy', color: '#c0392b', icon: '🌪️' };
    return { scale: 9, name: 'Huragan', color: '#8e44ad', icon: '🌪️' };
  }

  _calcWindPower(speedKmh, diameterM) {
    // P = 0.5 × ρ × A × v³ × Cp
    const rho = 1.225; // air density kg/m³
    const v = speedKmh / 3.6; // m/s
    const A = Math.PI * Math.pow(diameterM / 2, 2); // swept area m²
    const Cp = 0.35; // efficiency for small turbines
    return 0.5 * rho * A * Math.pow(v, 3) * Cp; // watts
  }

  _updateWindTab() {
    if (!this._hass?.states) return;
    const container = this.shadowRoot.getElementById('wind-live-data');
    if (!container) return;

    const n = (id) => { const st = this._hass.states[id]; return (st && st.state !== 'unknown' && st.state !== 'unavailable') ? parseFloat(st.state) : null; };

    const wind = n('sensor.ecowitt_wind_speed_9747');
    const gust = n('sensor.ecowitt_wind_gust_9747');
    const windDir = n('sensor.ecowitt_wind_direction_9747');
    const temp = n('sensor.ecowitt_outdoor_temp_9747');
    const pressure = n('sensor.ecowitt_pressure_relative');

    const windDirLabel = (deg) => {
      if (deg === null) return '—';
      const dirs = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];
      return dirs[Math.round(deg / 22.5) % 16];
    };

    const beaufort = this._getBeaufort(wind || 0);

    // Wind speed card — dual units km/h + m/s
    this._setText('wind-speed-val', wind !== null ? wind.toFixed(1) : '—');
    this._setText('wind-speed-ms', wind !== null ? (wind / 3.6).toFixed(1) + ' m/s' : '');
    this._setText('wind-gust-val', gust !== null ? gust.toFixed(1) : '—');
    this._setText('wind-gust-ms', gust !== null ? '(' + (gust / 3.6).toFixed(1) + ' m/s)' : '');
    this._setText('wind-beaufort-name', `${beaufort.icon} ${beaufort.name}`);
    const bfEl = this.shadowRoot.getElementById('wind-beaufort-name');
    if (bfEl) bfEl.style.color = beaufort.color;
    this._setText('wind-beaufort-scale', `${beaufort.scale} Bft`);

    // Direction + compass
    this._setText('wind-dir-val', windDir !== null ? `${windDir}° ${windDirLabel(windDir)}` : '—');
    const needle = this.shadowRoot.getElementById('wind-compass-needle');
    if (needle && windDir !== null) needle.setAttribute('transform', `rotate(${windDir}, 60, 60)`);

    // Instantaneous power potential
    const g = (id) => parseFloat(this.shadowRoot.getElementById(id)?.value) || 0;
    const diameter = g('wind-turbine-diameter') || 3.2;
    const cutIn = g('wind-turbine-cutin') || 3;
    const ratedSpeed = g('wind-turbine-rated') || 12;
    const nominalPower = (g('wind-turbine-power') || 3) * 1000; // W
    const windMs = (wind || 0) / 3.6;

    let instantPower = 0;
    if (windMs >= cutIn) {
      instantPower = this._calcWindPower(wind || 0, diameter);
      if (instantPower > nominalPower) instantPower = nominalPower;
    }

    this._setText('wind-instant-power', instantPower >= 1000 ? `${(instantPower / 1000).toFixed(2)} kW` : `${Math.round(instantPower)} W`);
    const powerBar = this.shadowRoot.getElementById('wind-power-bar-fill');
    if (powerBar) {
      const pct = Math.min(100, (instantPower / nominalPower) * 100);
      powerBar.style.width = `${pct}%`;
      powerBar.style.background = pct > 80 ? 'linear-gradient(90deg, #2ecc71, #27ae60)' : pct > 40 ? 'linear-gradient(90deg, #f7b731, #f39c12)' : 'linear-gradient(90deg, #e74c3c, #c0392b)';
    }
    this._setText('wind-power-pct', `${Math.min(100, (instantPower / nominalPower * 100)).toFixed(0)}%`);

    // Operating status
    const statusEl = this.shadowRoot.getElementById('wind-turbine-status');
    if (statusEl) {
      if (windMs < cutIn) {
        statusEl.textContent = '⏸️ Poniżej progu startu';
        statusEl.style.color = '#e74c3c';
      } else if (windMs >= cutIn && instantPower < nominalPower) {
        statusEl.textContent = '🔄 Produkcja częściowa';
        statusEl.style.color = '#f7b731';
      } else {
        statusEl.textContent = '⚡ Pełna moc!';
        statusEl.style.color = '#2ecc71';
      }
    }

    // Daily estimation
    const avgWindToday = wind || 0;
    const dailyHours = 24;
    let avgPower = 0;
    if ((avgWindToday / 3.6) >= cutIn) {
      avgPower = this._calcWindPower(avgWindToday, diameter);
      if (avgPower > nominalPower) avgPower = nominalPower;
    }
    const dailyKwh = (avgPower * dailyHours) / 1000;
    const priceKwh = g('wind-turbine-price') || 0.87;
    this._setText('wind-daily-est', `${dailyKwh.toFixed(2)} kWh`);
    this._setText('wind-daily-revenue', `${(dailyKwh * priceKwh).toFixed(2)} zł`);

    // Recalc profitability
    this._recalcWindProfitability();
  }

  /* ── Wind Historical Stats (Recorder API) ─── */
  async _fetchWindHistoricalStats() {
    if (this._windHistData && (Date.now() - (this._windHistFetchedAt || 0)) < 300000) {
      this._applyWindHistData();
      return;
    }
    if (!this._hass) return;

    const subtitleEl = this.shadowRoot.getElementById('wind-monthly-subtitle');
    if (subtitleEl) subtitleEl.textContent = '⏳ Pobieranie danych historycznych z Recorder...';

    try {
      const now = new Date();
      const start = new Date(now);
      start.setDate(start.getDate() - 365);
      start.setHours(0, 0, 0, 0);

      const stats = await this._hass.callWS({
        type: 'recorder/statistics_during_period',
        start_time: start.toISOString(),
        end_time: now.toISOString(),
        statistic_ids: ['sensor.ecowitt_wind_speed_9747'],
        period: 'hour',
        types: ['mean'],
      });

      const arr = stats['sensor.ecowitt_wind_speed_9747'] || [];
      if (arr.length === 0) {
        console.warn('Smarting HOME Wind: No Recorder data found for ecowitt_wind_speed_9747');
        this._windHistData = null;
        if (subtitleEl) subtitleEl.textContent = 'Brak danych historycznych — wyświetlam profil szacunkowy dla Polski.';
        this._applyWindHistData();
        return;
      }

      // Group by month → compute average wind speed per month
      const monthlyBuckets = {};
      let totalSum = 0, totalCount = 0;
      const uniqueDays = new Set();

      arr.forEach(s => {
        if (s.mean === null || s.mean === undefined) return;
        const d = new Date(s.start);
        const mKey = d.getMonth();
        const dKey = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;

        if (!monthlyBuckets[mKey]) monthlyBuckets[mKey] = { sum: 0, count: 0 };
        monthlyBuckets[mKey].sum += s.mean;
        monthlyBuckets[mKey].count++;

        totalSum += s.mean;
        totalCount++;
        uniqueDays.add(dKey);
      });

      const overallAvgKmh = totalCount > 0 ? totalSum / totalCount : 0;
      const daysCollected = uniqueDays.size;

      // Build monthly averages (km/h)
      const monthlyAvg = {};
      for (const [m, b] of Object.entries(monthlyBuckets)) {
        monthlyAvg[parseInt(m)] = b.sum / b.count;
      }

      this._windHistData = { monthlyAvg, overallAvgKmh, daysCollected, totalRecords: arr.length };
      this._windHistFetchedAt = Date.now();
      console.log(`Smarting HOME Wind: Loaded ${arr.length} hourly records from ${daysCollected} days, avg ${overallAvgKmh.toFixed(1)} km/h`);

      if (subtitleEl) subtitleEl.textContent = `Na podstawie rzeczywistych danych z Ecowitt WH90 (${daysCollected} dni pomiarów).`;

      this._applyWindHistData();
    } catch (e) {
      console.warn('Smarting HOME Wind: Recorder query failed', e);
      this._windHistData = null;
      if (subtitleEl) subtitleEl.textContent = 'Błąd pobierania danych — wyświetlam profil szacunkowy.';
      this._applyWindHistData();
    }
  }

  _applyWindHistData() {
    this._recalcWindProfitability();
    this._renderWindMonthlyChart();
  }

  _recalcWindProfitability() {
    const g = (id) => parseFloat(this.shadowRoot.getElementById(id)?.value) || 0;
    const nominalKw = g('wind-turbine-power') || 3;
    const diameter = g('wind-turbine-diameter') || 3.2;
    const investment = g('wind-turbine-investment') || 25000;
    const priceKwh = g('wind-turbine-price') || 0.87;
    const cutIn = g('wind-turbine-cutin') || 3;

    // Use historical average if available, otherwise fall back to current reading
    let userWindKmh = 0;
    let dataSource = 'bieżący odczyt';
    let daysInfo = '';
    if (this._windHistData && this._windHistData.overallAvgKmh > 0) {
      userWindKmh = this._windHistData.overallAvgKmh;
      dataSource = 'średnia historyczna';
      daysInfo = ` (${this._windHistData.daysCollected} dni)`;
    } else {
      const n = (id) => { const st = this._hass?.states?.[id]; return (st && st.state !== 'unknown' && st.state !== 'unavailable') ? parseFloat(st.state) : null; };
      userWindKmh = n('sensor.ecowitt_wind_speed_9747') || 0;
    }

    // Average wind classes for Poland (m/s)
    const windClasses = [
      { name: 'Słaby (3 m/s)', speed: 10.8 },
      { name: 'Umiarkowany (4 m/s)', speed: 14.4 },
      { name: 'Dobry (5 m/s)', speed: 18 },
      { name: 'Bardzo dobry (6 m/s)', speed: 21.6 },
      { name: 'Twoja lokalizacja', speed: userWindKmh },
    ];

    const profitEl = this.shadowRoot.getElementById('wind-profit-cards');
    if (!profitEl) return;

    const cards = windClasses.map((wc, i) => {
      const isUser = i === windClasses.length - 1;
      const windMs = wc.speed / 3.6;
      let avgPower = 0;
      if (windMs >= cutIn) {
        avgPower = this._calcWindPower(wc.speed, diameter);
        if (avgPower > nominalKw * 1000) avgPower = nominalKw * 1000;
      }
      // Capacity factor: for reference classes use fixed values, for user calculate from real wind
      let capacityFactor;
      if (isUser) {
        capacityFactor = nominalKw > 0 ? Math.min(0.45, avgPower / (nominalKw * 1000)) : 0;
      } else {
        capacityFactor = i === 0 ? 0.12 : i === 1 ? 0.18 : i === 2 ? 0.22 : 0.28;
      }
      const yearlyKwh = nominalKw * 8760 * capacityFactor;
      const yearlySavings = yearlyKwh * priceKwh;
      const payback = investment > 0 && yearlySavings > 0 ? investment / yearlySavings : null;
      const profit20 = yearlySavings * 20 - investment;

      const color = isUser ? '#00d4ff' : (payback && payback <= 10 ? '#2ecc71' : payback && payback <= 15 ? '#f7b731' : '#e74c3c');
      const bg = isUser ? 'rgba(0,212,255,0.08)' : 'rgba(255,255,255,0.03)';
      const border = isUser ? 'rgba(0,212,255,0.25)' : 'rgba(255,255,255,0.06)';

      return `<div style="background:${bg}; border:1px solid ${border}; border-radius:14px; padding:16px; position:relative">
        ${isUser ? `<div style="position:absolute; top:8px; right:8px; font-size:8px; color:#00d4ff; font-weight:700">📍 TWOJA LOKALIZACJA${daysInfo}</div>` : ''}
        <div style="font-size:12px; font-weight:700; color:${color}; margin-bottom:8px">${wc.name}</div>
        ${isUser ? `<div style="font-size:9px; color:#64748b; margin-bottom:6px">Źródło: ${dataSource} · ${(userWindKmh / 3.6).toFixed(1)} m/s · ${userWindKmh.toFixed(1)} km/h</div>` : ''}
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:4px; font-size:10px">
          <div style="color:#64748b">Roczna produkcja:</div><div style="color:#f7b731; font-weight:600">${yearlyKwh.toFixed(0)} kWh</div>
          <div style="color:#64748b">Roczne oszczędności:</div><div style="color:#2ecc71; font-weight:600">${yearlySavings.toFixed(0)} zł</div>
          <div style="color:#64748b">Zwrot inwestycji:</div><div style="color:${payback && payback <= 10 ? '#2ecc71' : payback && payback <= 15 ? '#f7b731' : '#e74c3c'}; font-weight:700">${payback ? `~${payback.toFixed(1)} lat` : '—'}</div>
          <div style="color:#64748b">Zysk w 20 lat:</div><div style="color:${profit20 >= 0 ? '#2ecc71' : '#e74c3c'}; font-weight:700">${profit20 >= 0 ? '+' : ''}${Math.round(profit20).toLocaleString('pl-PL')} zł</div>
        </div>
        ${investment > 0 ? `<div style="margin-top:8px"><div style="background:rgba(255,255,255,0.08); border-radius:6px; height:6px; overflow:hidden"><div style="height:100%; width:${payback ? Math.min(100, (20 / payback) * 5) : 0}%; background:linear-gradient(90deg, ${color}, #00d4ff); border-radius:6px"></div></div></div>` : ''}
      </div>`;
    });

    profitEl.innerHTML = cards.join('');

    // Recommendation — based on historical average
    const recEl = this.shadowRoot.getElementById('wind-recommendation');
    if (recEl && userWindKmh > 0) {
      const avgMs = userWindKmh / 3.6;
      const srcLabel = this._windHistData ? `na podstawie ${this._windHistData.daysCollected} dni pomiarów` : 'na podstawie bieżącego odczytu';
      if (avgMs >= 5) {
        recEl.innerHTML = `<div style="font-size:48px; text-align:center; margin-bottom:8px">✅</div>
          <div style="font-size:16px; font-weight:800; color:#2ecc71; text-align:center">Lokalizacja KORZYSTNA</div>
          <div style="font-size:11px; color:#94a3b8; text-align:center; margin-top:4px; line-height:1.6">Średnia prędkość wiatru ${avgMs.toFixed(1)} m/s (${srcLabel}) jest wystarczająca do opłacalnej eksploatacji przydomowej turbiny wiatrowej. Czas zwrotu inwestycji może wynosić poniżej 10 lat.</div>`;
      } else if (avgMs >= 3.5) {
        recEl.innerHTML = `<div style="font-size:48px; text-align:center; margin-bottom:8px">⚠️</div>
          <div style="font-size:16px; font-weight:800; color:#f7b731; text-align:center">Lokalizacja UMIARKOWANA</div>
          <div style="font-size:11px; color:#94a3b8; text-align:center; margin-top:4px; line-height:1.6">Średnia prędkość wiatru ${avgMs.toFixed(1)} m/s (${srcLabel}) jest na granicy opłacalności. Rozważ turbinę o niskim progu startu (cut-in < 2.5 m/s) lub hybrydę PV + wiatr.</div>`;
      } else {
        recEl.innerHTML = `<div style="font-size:48px; text-align:center; margin-bottom:8px">❌</div>
          <div style="font-size:16px; font-weight:800; color:#e74c3c; text-align:center">Lokalizacja NIEKORZYSTNA</div>
          <div style="font-size:11px; color:#94a3b8; text-align:center; margin-top:4px; line-height:1.6">Średnia prędkość wiatru ${avgMs.toFixed(1)} m/s (${srcLabel}) jest zbyt niska dla opłacalnej turbiny wiatrowej. Zalecamy inwestycję w fotowoltaikę lub system hybrydowy z magazynem energii.</div>`;
      }
    }

    // Monthly bar chart
    this._renderWindMonthlyChart();
    // Auto-save wind data with debounce (1.5s) — skip during initial load
    if (!this._windLoading) {
      if (this._windSaveTimeout) clearTimeout(this._windSaveTimeout);
      this._windSaveTimeout = setTimeout(() => this._saveWindData(), 1500);
    }
  }

  _renderWindMonthlyChart() {
    const chartEl = this.shadowRoot.getElementById('wind-monthly-chart');
    if (!chartEl) return;

    const monthNames = ['Sty','Lut','Mar','Kwi','Maj','Cze','Lip','Sie','Wrz','Paź','Lis','Gru'];
    const g = (id) => parseFloat(this.shadowRoot.getElementById(id)?.value) || 0;
    const nominalKw = g('wind-turbine-power') || 3;
    const diameter = g('wind-turbine-diameter') || 3.2;
    const cutIn = g('wind-turbine-cutin') || 3;

    // Fallback: hardcoded monthly capacity factors for Poland
    const fallbackFactors = [0.28, 0.26, 0.24, 0.20, 0.16, 0.14, 0.12, 0.13, 0.16, 0.22, 0.26, 0.28];
    const hasHistData = this._windHistData && Object.keys(this._windHistData.monthlyAvg).length > 0;

    const monthlyData = monthNames.map((name, i) => {
      let kwh;
      if (hasHistData && this._windHistData.monthlyAvg[i] !== undefined) {
        // Real data: compute kWh from average wind speed in this month
        const avgKmh = this._windHistData.monthlyAvg[i];
        const avgMs = avgKmh / 3.6;
        let power = 0;
        if (avgMs >= cutIn) {
          power = this._calcWindPower(avgKmh, diameter);
          if (power > nominalKw * 1000) power = nominalKw * 1000;
        }
        kwh = (power * 730) / 1000; // ~730 hours per month
      } else {
        // Fallback
        kwh = nominalKw * 730 * fallbackFactors[i];
      }
      const isReal = hasHistData && this._windHistData.monthlyAvg[i] !== undefined;
      return { month: name, kwh, isReal };
    });

    const maxKwh = Math.max(...monthlyData.map(d => d.kwh), 1);
    const currentMonth = new Date().getMonth();

    chartEl.innerHTML = monthlyData.map((d, i) => {
      const h = Math.max(4, (d.kwh / maxKwh) * 140);
      const isCurrent = i === currentMonth;
      const barColor = d.isReal
        ? (isCurrent ? 'linear-gradient(180deg, #00d4ff, #0099cc)' : 'linear-gradient(180deg, rgba(0,212,255,0.6), rgba(0,212,255,0.25))')
        : (isCurrent ? 'linear-gradient(180deg, #f39c12, #e67e22)' : 'linear-gradient(180deg, rgba(243,156,18,0.3), rgba(243,156,18,0.1))');
      const labelColor = d.isReal ? '#00d4ff' : '#f39c12';
      return `<div style="flex:1; display:flex; flex-direction:column; align-items:center; gap:2px">
        <div style="font-size:9px; font-weight:700; color:${labelColor}">${d.kwh.toFixed(0)}</div>
        <div style="width:100%; max-width:28px; height:${h}px; background:${barColor}; border-radius:4px 4px 0 0; ${isCurrent ? 'box-shadow: 0 0 8px rgba(0,212,255,0.4)' : ''}"></div>
        <div style="font-size:8px; color:${isCurrent ? '#00d4ff' : '#64748b'}; font-weight:${isCurrent ? '700' : '400'}">${d.month}</div>
      </div>`;
    }).join('');

    // Legend
    const legendEl = this.shadowRoot.getElementById('wind-chart-legend');
    if (legendEl) {
      if (hasHistData) {
        const realCount = monthlyData.filter(d => d.isReal).length;
        const estCount = 12 - realCount;
        legendEl.innerHTML = `<span style="color:#00d4ff">■</span> Dane z Recorder (${realCount} mies.)${estCount > 0 ? ` <span style="margin-left:8px; color:#f39c12">■</span> Szacunek (${estCount} mies.)` : ''}`;
      } else {
        legendEl.innerHTML = '<span style="color:#f39c12">■</span> Szacunek — brak danych historycznych';
      }
    }
  }

  _calcHEMSScore() {
    const el = this.shadowRoot.getElementById("hems-score-display");
    if (!el) return;

    // ── Factor 1: Autarky (30%) — how much energy comes from PV vs grid
    const impToday = this._nm("grid_import_today") || 0;
    const pvToday = parseFloat(this._fm("pv_today")) || 0;
    let autarky = 0;
    if (pvToday > 0 || impToday > 0) {
      const total = pvToday + impToday;
      autarky = total > 0 ? Math.min(100, ((total - impToday) / total) * 100) : 0;
    } else {
      const pvNow = this._nm("pv_power") || 0;
      const loadNow = this._nm("load_power") || 0;
      autarky = loadNow > 0 ? Math.min(100, (pvNow / loadNow) * 100) : 0;
    }

    // ── Factor 2: Self-consumption (25%) — how much PV is used vs exported
    const expToday = this._nm("grid_export_today") || 0;
    let selfCons = 0;
    if (pvToday > 0) {
      selfCons = Math.min(100, ((pvToday - expToday) / pvToday) * 100);
    } else {
      selfCons = (this._nm("pv_power") || 0) > 0 ? 100 : 0;
    }

    // ── Factor 3: Battery utilization (15%) — SOC management
    const soc = this._nm("battery_soc") || 0;
    // Optimal SOC range: 20-90%. Penalize extremes
    let battScore = 0;
    if (soc >= 20 && soc <= 90) battScore = 100;
    else if (soc > 90) battScore = 100 - (soc - 90) * 5; // slight penalty for overcharging
    else battScore = soc * 5; // 0% SOC = 0, 20% SOC = 100

    // ── Factor 4: Tariff optimization (15%) — are we using cheap energy?
    const hour = new Date().getHours();
    const isOffPeak = (hour >= 22 || hour < 6) || (new Date().getDay() === 0 || new Date().getDay() === 6);
    const gridPower = Math.abs(this._nm("grid_power") || 0);
    let tariffScore = 100; // default: good
    if (gridPower > 100) { // significant grid usage
      if (!isOffPeak && hour >= 13 && hour <= 15) tariffScore = 90; // midday cheaper
      else if (!isOffPeak && (hour >= 7 && hour < 13)) tariffScore = 40; // morning peak
      else if (!isOffPeak && (hour >= 15 && hour < 22)) tariffScore = 20; // afternoon peak!
      // Off-peak importing is OK = 100
    }
    // Bonus: if we're exporting during high RCE, great
    const rceSell = parseFloat(this._s("sensor.rce_pse_cena_sprzedazy") || "0");
    if (rceSell > 0.5 && expToday > 0) tariffScore = Math.min(100, tariffScore + 20);

    // ── Factor 5: PV yield vs forecast (15%)
    const forecastToday = this._n("sensor.smartinghome_pv_forecast_today_total") || 0;
    let pvYieldScore = 50; // neutral default
    if (forecastToday > 0 && pvToday > 0) {
      pvYieldScore = Math.min(100, (pvToday / forecastToday) * 100);
    } else if (hour < 7) {
      pvYieldScore = 50; // too early to judge
    }

    // ── Weighted score
    const score = Math.round(
      autarky * 0.30 +
      selfCons * 0.25 +
      battScore * 0.15 +
      tariffScore * 0.15 +
      pvYieldScore * 0.15
    );
    const clampedScore = Math.min(100, Math.max(0, score));

    // Store for AI
    this._hemsScore = clampedScore;

    // Color grading
    let scoreColor, scoreLabel, scoreBg;
    if (clampedScore >= 80) { scoreColor = '#2ecc71'; scoreLabel = '🟢 Doskonale'; scoreBg = 'rgba(46,204,113,0.08)'; }
    else if (clampedScore >= 60) { scoreColor = '#f7b731'; scoreLabel = '🟡 Dobrze'; scoreBg = 'rgba(247,183,49,0.08)'; }
    else if (clampedScore >= 40) { scoreColor = '#f39c12'; scoreLabel = '🟠 Przeciętnie'; scoreBg = 'rgba(243,156,18,0.08)'; }
    else { scoreColor = '#e74c3c'; scoreLabel = '🔴 Słabo'; scoreBg = 'rgba(231,76,60,0.08)'; }

    el.innerHTML = `
      <div style="display:flex; align-items:center; gap:16px">
        <div style="position:relative; width:80px; height:80px; flex-shrink:0">
          <svg viewBox="0 0 36 36" style="width:80px; height:80px; transform:rotate(-90deg)">
            <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="3"/>
            <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="${scoreColor}" stroke-width="3" stroke-dasharray="${clampedScore}, 100" stroke-linecap="round" style="transition:stroke-dasharray 1s ease"/>
          </svg>
          <div style="position:absolute; inset:0; display:flex; align-items:center; justify-content:center; flex-direction:column">
            <div style="font-size:22px; font-weight:900; color:${scoreColor}">${clampedScore}</div>
            <div style="font-size:8px; color:#64748b; margin-top:-2px">/ 100</div>
          </div>
        </div>
        <div style="flex:1">
          <div style="font-size:14px; font-weight:700; color:${scoreColor}; margin-bottom:6px">${scoreLabel}</div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:4px 12px; font-size:10px">
            <div style="color:#94a3b8">⚡ Autarkia</div><div style="color:#fff; font-weight:600">${autarky.toFixed(0)}%</div>
            <div style="color:#94a3b8">♻️ Autokonsumpcja</div><div style="color:#fff; font-weight:600">${selfCons.toFixed(0)}%</div>
            <div style="color:#94a3b8">🔋 Bateria</div><div style="color:#fff; font-weight:600">${battScore.toFixed(0)}%</div>
            <div style="color:#94a3b8">💰 Taryfa</div><div style="color:#fff; font-weight:600">${tariffScore.toFixed(0)}%</div>
            <div style="color:#94a3b8">☀️ PV Yield</div><div style="color:#fff; font-weight:600">${pvYieldScore.toFixed(0)}%</div>
          </div>
        </div>
      </div>`;
  }

  /* ── HEMS Arbitrage: toggle collapsible sections ─── */
  _toggleHEMSSection(layer) {
    const el = this.shadowRoot.getElementById(`hems-layer-${layer}`);
    if (el) el.classList.toggle('collapsed');
  }

  /* ── HEMS Arbitrage: update all automation card statuses ─── */
  _updateHEMSArbitrage() {
    if (this._activeTab !== 'hems') return;
    const now = new Date();
    const hour = now.getHours();
    const min = now.getMinutes();
    const dow = now.getDay();
    const month = now.getMonth();
    const isWeekend = (dow === 0 || dow === 6);
    const isSummer = (month >= 3 && month <= 8);
    const tariff = this._settings.tariff_plan || "G13";

    // Helper: get sensor values
    const soc = this._nm("battery_soc") || 0;
    const pvPower = this._nm("pv_power") || 0;
    const gridPower = this._nm("grid_power") || 0;
    const surplus = pvPower - (this._nm("load_power") || 0);
    const v1 = this._nm("voltage_l1") || 0;
    const v2 = this._nm("voltage_l2") || 0;
    const v3 = this._nm("voltage_l3") || 0;
    const vMax = Math.max(v1, v2, v3);

    // RCE data
    const rceMwh = parseFloat(this._s("sensor.rce_pse_cena") || "0");
    const rceKwh = parseFloat(this._s("sensor.rce_pse_cena_za_kwh") || "0");
    const rceNext = parseFloat(this._s("sensor.rce_pse_cena_nastepnej_godziny") || "0");
    const rceCheapWin = this._s("binary_sensor.rce_pse_aktywne_najtansze_okno_dzisiaj");
    const rceExpWin = this._s("binary_sensor.rce_pse_aktywne_najdrozsze_okno_dzisiaj");

    // Forecast & Weather
    const fcstTmrw1 = parseFloat(this._s("sensor.energy_production_tomorrow") || "0");
    const fcstTmrw2 = parseFloat(this._s("sensor.energy_production_tomorrow_2") || "0");
    const fcstTmrw = fcstTmrw1 + fcstTmrw2;
    const fcstRem1 = parseFloat(this._s("sensor.energy_production_today_remaining") || "0");
    const fcstRem2 = parseFloat(this._s("sensor.energy_production_today_remaining_2") || "0");
    const fcstRem = fcstRem1 + fcstRem2;
    const fcstToday1 = parseFloat(this._s("sensor.energy_production_today") || "0");
    const fcstToday2 = parseFloat(this._s("sensor.energy_production_today_2") || "0");
    const fcstToday = fcstToday1 + fcstToday2;
    const radiation = parseFloat(this._s("sensor.ecowitt_solar_radiation_9747") || "0");
    const rainRate = parseFloat(this._s("sensor.ecowitt_rain_rate_9747") || "0");
    const uvIndex = parseFloat(this._s("sensor.ecowitt_uv_index_9747") || "0");

    // Device states
    const boilerState = this._s("switch.bojler_3800") || "unknown";
    const acState = this._s("switch.klimatyzacja_socket_1") || "unknown";
    const pumpState = this._s("switch.pompa_zalania_socket_1") || "unknown";
    const invMode = this._s("select.goodwe_tryb_pracy_falownika") || "—";
    const hemsMode = this._s("sensor.smartinghome_hems_mode") || "auto";

    // ── Tariff zone calculation (dynamic) ──
    const tInfo = this._getTariffInfo();
    const g13Zone = tInfo.zoneName;
    const g13Price = tInfo.price;

    // ── HEADER & KPIs ──
    const modeMap = { auto: "AUTO", sell: "MAX SELL", charge: "CHARGE", peak_save: "PEAK SAVE", night_arbitrage: "NOC ARB", emergency: "EMERGENCY", manual: "MANUAL" };
    this._setText("hems-arb-mode", modeMap[hemsMode] || hemsMode.toUpperCase());
    this._setText("hems-kpi-soc", `${soc}%`);
    const socEl = this.shadowRoot.getElementById("hems-kpi-soc");
    if (socEl) socEl.style.color = soc > 50 ? "#2ecc71" : soc > 20 ? "#f7b731" : "#e74c3c";
    this._setText("hems-kpi-rce", `${rceKwh.toFixed(2)} zł`);
    // Dynamic KPI label + value
    const g13LabelEl = this.shadowRoot.getElementById("hems-kpi-g13-label");
    if (g13LabelEl) g13LabelEl.textContent = `⏰ Strefa ${tInfo.tariff}`;
    this._setText("hems-kpi-g13", g13Zone);
    const g13El = this.shadowRoot.getElementById("hems-kpi-g13");
    if (g13El) g13El.style.color = tInfo.zoneColor;
    this._setText("hems-kpi-inv", invMode.replace(/_/g, " ").toUpperCase());

    // License tier badge
    const tierEl = this.shadowRoot.getElementById("hems-arb-tier");
    const licTier = this._tier();
    if (tierEl) tierEl.textContent = licTier;

    // ── Dynamic tariff labels for W1 layer + battery arbitrage ──
    const w1Name = this.shadowRoot.getElementById("hems-w1-name");
    if (w1Name) {
      if (tInfo.tariff === "Dynamic") w1Name.textContent = "Ceny Dynamiczne ENTSO-E";
      else w1Name.textContent = `Harmonogram ${tInfo.tariff}`;
    }

    // W1 card descriptions — adapt to tariff
    const msDesc = this.shadowRoot.getElementById("hac-morning-sell-desc");
    const mcDesc = this.shadowRoot.getElementById("hac-midday-charge-desc");
    const epDesc = this.shadowRoot.getElementById("hac-evening-peak-desc");
    if (tInfo.tariff === "Dynamic") {
      const dynPrice = parseFloat(this._haState("sensor.entso_e_koszt_all_in_teraz")) || 0;
      const dynAvg = parseFloat(this._haState("sensor.entso_e_srednia_dzisiaj")) || 0;
      if (msDesc) msDesc.textContent = `Cena > średnia (${dynAvg.toFixed(2)} zł) → sprzedawaj / rozładowuj baterię.`;
      if (mcDesc) mcDesc.textContent = `Cena < średnia (${dynAvg.toFixed(2)} zł) → ładuj baterię.`;
      if (epDesc) epDesc.textContent = `Teraz: ${dynPrice.toFixed(2)} zł/kWh. Bateria reaguje na cenę rynkową.`;
    } else if (tInfo.tariff === "G13") {
      if (msDesc) msDesc.textContent = "G13 szczyt poranny (0.91 zł). Sprzedawaj 7-13 Pn-Pt.";
      if (mcDesc) mcDesc.textContent = "Off-peak (0.63 zł). Ładuj baterię 13:00-szczyt Pn-Pt.";
      if (epDesc) epDesc.textContent = "G13 szczyt (1.50 zł). Bateria zasila dom.";
    } else if (tInfo.tariff === "G12" || tInfo.tariff === "G12w") {
      if (msDesc) msDesc.textContent = `${tInfo.tariff} szczyt (1.10 zł). Sprzedawaj w szczycie Pn-Pt.`;
      if (mcDesc) mcDesc.textContent = "Off-peak (0.55 zł). Ładuj baterię 13-15 + 22-06.";
      if (epDesc) epDesc.textContent = `${tInfo.tariff} szczyt (1.10 zł). Bateria zasila dom.`;
    } else {
      if (msDesc) msDesc.textContent = "G11 stała cena. Strategia zależy od RCE.";
      if (mcDesc) mcDesc.textContent = "Stała cena — ładuj przy niskim RCE.";
      if (epDesc) epDesc.textContent = "Stała cena — bateria zasila dom przy drogim RCE.";
    }

    // Battery arbitrage strategy text
    const arbTitle = this.shadowRoot.getElementById("arb-strategy-title");
    const arbBody = this.shadowRoot.getElementById("arb-strategy-body");
    if (arbTitle) arbTitle.textContent = `📋 Strategia ${tInfo.tariff} + RCE`;
    if (arbBody) {
      if (tInfo.tariff === "G13") {
        arbBody.innerHTML = '<strong style="color:#2ecc71">⏰ 22:00–06:00</strong> — Ładuj baterię (off-peak, najtańsza strefa)<br>' +
          '<strong style="color:#e74c3c">⏰ 07:00–13:00</strong> — Rozładowuj na dom (peak poranny)<br>' +
          '<strong style="color:#f7b731">⏰ 13:00–17:00</strong> — PV ładuje baterię + eksport nadwyżki<br>' +
          '<strong style="color:#e74c3c">⏰ 17:00–22:00</strong> — Rozładowuj na dom (peak wieczorny, najdrożej!)';
      } else if (tInfo.tariff === "G12" || tInfo.tariff === "G12w") {
        arbBody.innerHTML = '<strong style="color:#2ecc71">⏰ 22:00–06:00 + 13:00–15:00</strong> — Ładuj baterię (off-peak)<br>' +
          '<strong style="color:#e74c3c">⏰ 06:00–13:00 + 15:00–22:00</strong> — Rozładowuj na dom (szczyt)' +
          (tInfo.tariff === "G12w" ? '<br><strong style="color:#2ecc71">🏖️ Weekend</strong> — Cały dzień off-peak' : '');
      } else {
        arbBody.innerHTML = '<strong style="color:#3b82f6">⏰ Cały dzień</strong> — Stała cena (0.87 zł/kWh)<br>' +
          '<strong style="color:#2ecc71">💰 Strategia</strong> — Ładuj przy niskim RCE, sprzedawaj przy wysokim RCE';
      }
    }

    // ═══ W1: SCHEDULE ═══
    const setStatus = (id, status) => {
      const el = this.shadowRoot.getElementById(id);
      const card = this.shadowRoot.getElementById(id.replace('-st', ''));
      if (!el) return;
      if (status === 'on') {
        el.textContent = "● AKTYWNE";
        el.className = "hac-status on";
        if (card) card.classList.add("is-active");
      } else if (status === 'wait') {
        el.textContent = "◐ CZEKA";
        el.className = "hac-status wait";
        if (card) card.classList.remove("is-active");
      } else {
        el.textContent = "○ IDLE";
        el.className = "hac-status off";
        if (card) card.classList.remove("is-active");
      }
    };

    // ═══ W0: GRID IMPORT GUARD ═══
    const batPower = this._nm("battery_power") || 0;
    const isWorkday = !isWeekend;
    const isExpensiveHour = isWorkday && (tInfo.zone === 'morning' || tInfo.zone === 'peak');
    const gridImporting = gridPower > 100;
    const batCharging = batPower > 100;
    // Guard active: importing from grid + charging battery + expensive hour + RCE > 100
    setStatus("hac-grid-guard-st", isExpensiveHour && gridImporting && batCharging && rceMwh > 100 ? "on" : (isExpensiveHour ? "wait" : "off"));
    this._setText("hac-gg-grid", `${gridPower > 0 ? '+' : ''}${(gridPower/1000).toFixed(1)} kW`);
    this._setText("hac-gg-bat", `${batPower > 0 ? 'Ładuje' : 'Rozładowuje'} ${Math.abs(batPower).toFixed(0)} W`);

    // PV Surplus Smart Charge: export > 300W + expensive hour + SOC < 95
    const gridExporting = gridPower < -300;
    setStatus("hac-pv-surplus-st", isExpensiveHour && gridExporting && soc < 95 ? "on" : (isExpensiveHour && pvPower > 500 ? "wait" : "off"));
    this._setText("hac-ps-grid", `${gridPower > 0 ? '+' : ''}${(gridPower/1000).toFixed(1)} kW`);
    this._setText("hac-ps-soc", `${soc}%`);

    // ═══ W1: G13 SCHEDULE ═══
    const peakStart = isSummer ? 19 : 16;
    const peakEnd = isSummer ? 22 : 21;

    // Morning sell (7-13 Pn-Pt)
    setStatus("hac-morning-sell-st", isWorkday && hour >= 7 && hour < 13 ? "on" : isWorkday ? "wait" : "off");
    this._setText("hac-ms-rce", `${rceKwh.toFixed(2)} zł`);
    this._setText("hac-ms-soc", `${soc}%`);

    // Midday charge (13-peakStart Pn-Pt)
    setStatus("hac-midday-charge-st", isWorkday && hour >= 13 && hour < peakStart ? "on" : isWorkday ? "wait" : "off");
    this._setText("hac-mc-rce", `${rceKwh.toFixed(2)} zł`);
    this._setText("hac-mc-soc", `${soc}%`);

    // Evening peak
    setStatus("hac-evening-peak-st", isWorkday && hour >= peakStart && hour < peakEnd ? "on" : isWorkday ? "wait" : "off");
    this._setText("hac-ep-g13", `${g13Zone} (${g13Price} zł)`);
    this._setText("hac-ep-soc", `${soc}%`);

    // Weekend
    setStatus("hac-weekend-st", isWeekend ? "on" : "off");
    this._setText("hac-wk-rce", `${rceKwh.toFixed(2)} zł`);
    this._setText("hac-wk-cheap", rceCheapWin === "on" ? "✅ TAK" : "—");

    // Night arbitrage
    const nightActive = (hour >= 23 || hour < 6) && soc < 90 && fcstToday < 8;
    setStatus("hac-night-arb-st", nightActive ? "on" : (hour >= 20 ? "wait" : "off"));
    this._setText("hac-na-fcst", `${fcstTmrw.toFixed(1)} kWh`);
    this._setText("hac-na-soc", `${soc}%`);

    // ═══ W2: RCE DYNAMIC ═══
    setStatus("hac-rce-cheap-st", rceCheapWin === "on" ? "on" : "off");
    this._setText("hac-rc-win", rceCheapWin === "on" ? "✅ AKTYWNE" : "—");
    this._setText("hac-rc-soc", `${soc}%`);

    setStatus("hac-rce-exp-st", rceExpWin === "on" ? "on" : "off");
    this._setText("hac-re-rce", `${rceMwh.toFixed(0)} PLN/MWh`);
    this._setText("hac-re-g13", g13Zone);

    setStatus("hac-rce-low-st", rceMwh < 150 && rceMwh > 0 && hour >= 7 && hour < 22 ? "on" : "off");
    this._setText("hac-rl-mwh", `${rceMwh.toFixed(0)} PLN`);
    const rceTrend = rceNext > rceMwh ? "📈 Rośnie" : rceNext < rceMwh ? "📉 Spada" : "→ Stabilna";
    this._setText("hac-rl-trend", rceTrend);

    setStatus("hac-rce-high-st", rceMwh > 300 && hour >= 7 && hour < 13 ? "on" : (rceMwh > 300 ? "wait" : "off"));
    this._setText("hac-rh-mwh", `${rceMwh.toFixed(0)} PLN`);
    this._setText("hac-rh-trend", rceTrend);

    const rceDoubleActive = rceMwh > 500 && hour >= peakStart && hour < peakEnd;
    setStatus("hac-rce-peak-st", rceDoubleActive ? "on" : "off");
    this._setText("hac-rp-rce", `${rceMwh.toFixed(0)} PLN/MWh`);
    this._setText("hac-rp-soc", `${soc}%`);

    setStatus("hac-rce-neg-st", rceMwh < 0 ? "on" : "off");
    this._setText("hac-rn-mwh", `${rceMwh.toFixed(0)} PLN`);
    this._setText("hac-rn-blr", boilerState === "on" ? "✅ ON" : "OFF");

    // ═══ W3: SOC SAFETY ═══
    setStatus("hac-soc-11-st", hour === 11 && soc < 50 ? "on" : (hour < 11 ? "wait" : "off"));
    this._setText("hac-s11-soc", `${soc}%`);

    setStatus("hac-soc-12-st", hour === 12 && soc < 70 ? "on" : (hour < 12 ? "wait" : "off"));
    this._setText("hac-s12-soc", `${soc}%`);

    setStatus("hac-soc-low-st", soc < 20 ? "on" : soc < 30 ? "wait" : "off");
    this._setText("hac-sl-soc", `${soc}%`);

    setStatus("hac-soc-emergency-st", soc < 5 ? "on" : soc < 10 ? "wait" : "off");
    this._setText("hac-se-soc", `${soc}%`);

    setStatus("hac-weak-fcst-st", fcstTmrw < 5 && hour >= 18 ? "on" : "off");
    this._setText("hac-wf-fcst", `${fcstTmrw.toFixed(1)} kWh`);
    this._setText("hac-wf-dod", fcstTmrw < 5 ? "70%" : "95%");

    setStatus("hac-restore-dod-st", pvPower > 500 ? "on" : "off");
    this._setText("hac-rd-pv", this._pw(pvPower));

    // ═══ W4: VOLTAGE + SURPLUS ═══
    setStatus("hac-volt-blr-st", vMax > 252 ? "on" : "off");
    this._setText("hac-vb-vmax", `${vMax.toFixed(1)} V`);
    this._setText("hac-vb-blr", boilerState === "on" ? "✅ ON" : "OFF");

    setStatus("hac-volt-ac-st", vMax > 253 ? "on" : "off");
    this._setText("hac-va-vmax", `${vMax.toFixed(1)} V`);
    this._setText("hac-va-ac", acState === "on" ? "✅ ON" : "OFF");

    setStatus("hac-volt-chrg-st", vMax > 254 ? "on" : "off");
    this._setText("hac-vc-vmax", `${vMax.toFixed(1)} V`);
    this._setText("hac-vc-soc", `${soc}%`);

    setStatus("hac-sur-blr-st", surplus > 2000 && soc > 80 ? "on" : (surplus > 1000 ? "wait" : "off"));
    this._setText("hac-sb-sur", this._pw(surplus));
    this._setText("hac-sb-soc", `${soc}%`);

    setStatus("hac-sur-ac-st", surplus > 3000 && soc > 85 && boilerState === "on" ? "on" : "off");
    this._setText("hac-sa-sur", this._pw(surplus));
    this._setText("hac-sa-soc", `${soc}%`);

    setStatus("hac-sur-sock-st", surplus > 4000 && soc > 90 && boilerState === "on" && acState === "on" ? "on" : "off");
    this._setText("hac-ss-sur", this._pw(surplus));
    this._setText("hac-ss-soc", `${soc}%`);

    setStatus("hac-emergency-st", soc < 50 && (boilerState === "on" || acState === "on") ? "on" : "off");
    this._setText("hac-em-soc", `${soc}%`);

    // ═══ W5: SMART PRE-PEAK ═══
    setStatus("hac-pp-0530-st", hour >= 5 && hour < 7 && soc < 80 && fcstToday < 10 ? "on" : (hour < 5 ? "wait" : "off"));
    this._setText("hac-p5-fcst", `${fcstToday.toFixed(1)} kWh`);
    this._setText("hac-p5-soc", `${soc}%`);

    setStatus("hac-pp-1000-st", hour >= 10 && hour < 11 && soc < 60 && radiation < 200 ? "on" : (hour < 10 ? "wait" : "off"));
    this._setText("hac-p10-rad", `${radiation.toFixed(0)} W/m²`);
    this._setText("hac-p10-uv", uvIndex.toFixed(1));

    setStatus("hac-pp-1330-st", !isSummer && hour >= 13 && hour < 14 && min >= 30 && soc < 80 && fcstRem < 5 ? "on" : (!isSummer && hour < 14 ? "wait" : "off"));
    this._setText("hac-p13-rem", `${fcstRem.toFixed(1)} kWh`);
    this._setText("hac-p13-soc", `${soc}%`);

    setStatus("hac-pp-1800-st", isSummer && hour >= 18 && hour < 19 && soc < 70 && radiation < 100 ? "on" : (isSummer && hour < 19 ? "wait" : "off"));
    this._setText("hac-p18-rad", `${radiation.toFixed(0)} W/m²`);
    this._setText("hac-p18-soc", `${soc}%`);

    setStatus("hac-pp-cloud-st", radiation < 50 && soc < 70 && hour >= 8 && hour < 18 ? "on" : "off");
    this._setText("hac-pc-rad", `${radiation.toFixed(0)} W/m²`);
    this._setText("hac-pc-pv", this._pw(pvPower));

    setStatus("hac-pp-rain-st", rainRate > 0.5 && soc < 70 ? "on" : "off");
    this._setText("hac-pr-rain", `${rainRate.toFixed(1)} mm/h`);
    this._setText("hac-pr-pv", this._pw(pvPower));

    // ═══ OTHER ═══
    const boilerSchedule = [6, 7, 14, 15, 17, 18];
    const boilerOn = (hour === 6 || hour === 14 || hour === 17);
    setStatus("hac-boiler-st", boilerState === "on" ? "on" : boilerOn ? "wait" : "off");
    this._setText("hac-bl-state", boilerState === "on" ? "✅ ON" : "OFF");
    const nextBoiler = boilerSchedule.find(h => h > hour) || boilerSchedule[0];
    this._setText("hac-bl-time", `Następny: ${String(nextBoiler).padStart(2,'0')}:00`);

    this._setText("hac-pm-sensor", "—");
    this._setText("hac-pm-pump", pumpState === "on" ? "✅ ON" : "OFF");
    setStatus("hac-pump-st", pumpState === "on" ? "on" : "off");

    setStatus("hac-report-st", hour === 21 ? "on" : (hour > 21 ? "off" : "wait"));
    const pvTodayVal = parseFloat(this._fm("pv_today")) || 0;
    const expTodayVal = this._nm("grid_export_today") || 0;
    const impTodayVal = this._nm("grid_import_today") || 0;
    this._setText("hac-rpt-pv", `${pvTodayVal.toFixed(1)} kWh`);
    const balance = expTodayVal - impTodayVal;
    this._setText("hac-rpt-bal", `${balance > 0 ? '+' : ''}${balance.toFixed(1)} kWh`);

    // Phase imbalance
    const pL1 = Math.abs(this._nm("power_l1") || 0);
    const pL2 = Math.abs(this._nm("power_l2") || 0);
    const pL3 = Math.abs(this._nm("power_l3") || 0);
    const phaseMax = Math.max(pL1, pL2, pL3);
    const phaseMin = Math.min(pL1, pL2, pL3);
    const phaseImbalance = phaseMax - phaseMin;
    setStatus("hac-phase-st", phaseImbalance > 3000 ? "on" : "off");
    this._setText("hac-ph-l1", `${(pL1/1000).toFixed(1)} kW`);
    this._setText("hac-ph-l2", `${(pL2/1000).toFixed(1)} kW`);

    // ── Count active automations per layer ──
    const countActive = (layerId) => {
      const body = this.shadowRoot.getElementById(layerId);
      if (!body) return { total: 0, active: 0 };
      const cards = body.querySelectorAll('.hems-auto-card');
      const active = body.querySelectorAll('.hems-auto-card.is-active');
      return { total: cards.length, active: active.length };
    };

    const w0 = countActive('hems-w0-body');
    const w1 = countActive('hems-w1-body');
    const w2 = countActive('hems-w2-body');
    const w3 = countActive('hems-w3-body');
    const w4 = countActive('hems-w4-body');
    const w5 = countActive('hems-w5-body');
    const other = countActive('hems-other-body');

    this._setText("hems-w0-count", `${w0.active}/${w0.total} aktywnych`);
    this._setText("hems-w1-count", `${w1.active}/${w1.total} aktywnych`);
    this._setText("hems-w2-count", `${w2.active}/${w2.total} aktywnych`);
    this._setText("hems-w3-count", `${w3.active}/${w3.total} aktywnych`);
    this._setText("hems-w4-count", `${w4.active}/${w4.total} aktywnych`);
    this._setText("hems-w5-count", `${w5.active}/${w5.total} aktywnych`);
    this._setText("hems-other-count", `${other.active}/${other.total} aktywnych`);

    // Overall active count
    const totalActive = w0.active + w1.active + w2.active + w3.active + w4.active + w5.active + other.active;
    const statusEl = this.shadowRoot.getElementById("hems-arb-status");
    if (statusEl) {
      statusEl.textContent = totalActive > 0 ? `● ${totalActive} AKTYWNYCH` : "○ STAN GOTOWOŚCI";
      statusEl.style.color = totalActive > 0 ? "#2ecc71" : "#64748b";
    }
  }

  _loadCronSettings() {
    const s = this._settings;
    const chkH = this.shadowRoot.getElementById("chk-cron-hems");
    const chkR = this.shadowRoot.getElementById("chk-cron-report");
    const chkA = this.shadowRoot.getElementById("chk-cron-anomaly");
    const chkAP = this.shadowRoot.getElementById("chk-cron-autopilot");
    if (chkH && s.cron_hems_enabled !== undefined) chkH.checked = s.cron_hems_enabled;
    if (chkR && s.cron_report_enabled !== undefined) chkR.checked = s.cron_report_enabled;
    if (chkA && s.cron_anomaly_enabled !== undefined) chkA.checked = s.cron_anomaly_enabled;
    if (chkAP && s.cron_autopilot_enabled !== undefined) chkAP.checked = s.cron_autopilot_enabled;
    const selH = this.shadowRoot.getElementById("sel-cron-hems");
    const selR = this.shadowRoot.getElementById("sel-cron-report");
    const selA = this.shadowRoot.getElementById("sel-cron-anomaly");
    const selAP = this.shadowRoot.getElementById("sel-cron-autopilot");
    if (selH && s.cron_hems_interval) selH.value = String(s.cron_hems_interval);
    if (selR && s.cron_report_interval) selR.value = String(s.cron_report_interval);
    if (selA && s.cron_anomaly_interval) selA.value = String(s.cron_anomaly_interval);
    if (selAP && s.cron_autopilot_interval) selAP.value = String(s.cron_autopilot_interval);
    this._updateCronStatus();
  }

  _updateCronStatus() {
    const s = this._settings;
    ["hems", "report", "anomaly", "autopilot"].forEach(job => {
      const key = job === "hems" ? "ai_hems_advice" : job === "report" ? "ai_daily_report" : job === "anomaly" ? "ai_anomaly_report" : "ai_autopilot_log";
      const el = this.shadowRoot.getElementById(`cron-status-${job}`);
      if (el && s[key] && s[key].timestamp) {
        el.textContent = "\u23f0 Ostatnio: " + s[key].timestamp;
        el.style.color = job === "autopilot" ? "#9b59b6" : "#2ecc71";
      }
    });
  }

  _renderMarkdown(text) {
    if (!text) return '';
    // Escape HTML first
    let t = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

    // ── Code blocks
    t = t.replace(/```([\s\S]*?)```/g, '<pre style="background:rgba(0,212,255,0.06);border:1px solid rgba(0,212,255,0.15);padding:10px 12px;border-radius:8px;font-size:11px;font-family:monospace;overflow-x:auto;margin:8px 0;color:#94a3b8"><code>$1</code></pre>');
    t = t.replace(/`([^`]+)`/g, '<code style="background:rgba(0,212,255,0.1);color:#00d4ff;padding:1px 5px;border-radius:4px;font-size:11px">$1</code>');

    // ── Tables: | col | col |
    t = t.replace(/(\|.+\|[\r\n]+\|[-| :]+\|[\r\n]+((\|.+\|[\r\n]*)+))/g, (match) => {
      const rows = match.trim().split('\n').filter(r => r.trim());
      if (rows.length < 2) return match;
      const headers = rows[0].split('|').filter(c => c.trim()).map(c => c.trim());
      const dataRows = rows.slice(2);
      let th = headers.map(h => `<th style="padding:6px 10px;text-align:left;font-weight:700;color:#00d4ff;font-size:10px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:1px solid rgba(255,255,255,0.1)">${h}</th>`).join('');
      let tbody = dataRows.map(r => {
        const cells = r.split('|').filter(c => c.trim()).map(c => c.trim());
        return '<tr>' + cells.map(c => `<td style="padding:5px 10px;font-size:12px;color:#cbd5e1;border-bottom:1px solid rgba(255,255,255,0.04)">${c}</td>`).join('') + '</tr>';
      }).join('');
      return `<table style="width:100%;border-collapse:collapse;margin:8px 0;background:rgba(255,255,255,0.02);border-radius:8px;overflow:hidden"><thead><tr>${th}</tr></thead><tbody>${tbody}</tbody></table>`;
    });

    // ── Blockquotes → Callout boxes
    t = t.replace(/^&gt; (.+)/gm, (match, content) => {
      let color = '#00d4ff', bg = 'rgba(0,212,255,0.06)', icon = '💡';
      if (content.match(/⚠️|uwag|ostrzeż/i)) { color = '#f7b731'; bg = 'rgba(247,183,49,0.08)'; icon = '⚠️'; }
      else if (content.match(/❌|kryty|niebezp|nie rób/i)) { color = '#e74c3c'; bg = 'rgba(231,76,60,0.08)'; icon = '🚫'; }
      else if (content.match(/✅|zalec|dobr|tak/i)) { color = '#2ecc71'; bg = 'rgba(46,204,113,0.08)'; icon = '✅'; }
      return `<div style="background:${bg};border-left:3px solid ${color};padding:8px 12px;border-radius:0 8px 8px 0;margin:6px 0;font-size:12px;color:#e2e8f0">${content}</div>`;
    });

    // ── Horizontal rules → section dividers
    t = t.replace(/^---+$/gm, '<div style="height:1px;background:linear-gradient(90deg,transparent,rgba(0,212,255,0.3),transparent);margin:12px 0"></div>');

    // ── H1: Main title with gradient
    t = t.replace(/^# (.+)$/gm, '<div style="font-size:16px;font-weight:800;color:#fff;margin:8px 0;padding-bottom:6px;border-bottom:2px solid rgba(0,212,255,0.3)">$1</div>');

    // ── H2: Section cards with colored border
    t = t.replace(/^## (.+)$/gm, (match, title) => {
      let borderColor = '#00d4ff';
      if (title.match(/bateria|battery|akumulator/i)) borderColor = '#2ecc71';
      else if (title.match(/sieć|grid|import|eksport/i)) borderColor = '#f7b731';
      else if (title.match(/analiz|diagnoz|status|stan/i)) borderColor = '#3498db';
      else if (title.match(/rekomend|zalec|porad|sugest/i)) borderColor = '#9b59b6';
      else if (title.match(/koszt|cena|taryf|finans|ekonom/i)) borderColor = '#e74c3c';
      else if (title.match(/PV|solar|słone|fotow/i)) borderColor = '#f39c12';
      return `<div style="font-size:14px;font-weight:700;color:#fff;margin:14px 0 6px;padding:8px 12px;background:rgba(255,255,255,0.03);border-left:3px solid ${borderColor};border-radius:0 8px 8px 0">${title}</div>`;
    });

    // ── H3: Sub-section headers
    t = t.replace(/^### (.+)$/gm, '<div style="font-size:12px;font-weight:700;color:#00d4ff;margin:10px 0 4px;padding-left:8px;border-left:2px solid rgba(0,212,255,0.3)">$1</div>');

    // ── Bold with emoji badges
    t = t.replace(/\*\*(✅[^*]+)\*\*/g, '<span style="display:inline-block;background:rgba(46,204,113,0.12);color:#2ecc71;padding:2px 8px;border-radius:6px;font-weight:700;font-size:11px;margin:1px 0">$1</span>');
    t = t.replace(/\*\*(⚠️[^*]+)\*\*/g, '<span style="display:inline-block;background:rgba(247,183,49,0.12);color:#f7b731;padding:2px 8px;border-radius:6px;font-weight:700;font-size:11px;margin:1px 0">$1</span>');
    t = t.replace(/\*\*(❌[^*]+)\*\*/g, '<span style="display:inline-block;background:rgba(231,76,60,0.12);color:#e74c3c;padding:2px 8px;border-radius:6px;font-weight:700;font-size:11px;margin:1px 0">$1</span>');
    t = t.replace(/\*\*(💡[^*]+)\*\*/g, '<span style="display:inline-block;background:rgba(0,212,255,0.12);color:#00d4ff;padding:2px 8px;border-radius:6px;font-weight:700;font-size:11px;margin:1px 0">$1</span>');
    t = t.replace(/\*\*Rekomendacja:\s*(.+?)\*\*/g, '<span style="display:inline-block;background:rgba(155,89,182,0.15);color:#c39bd3;padding:3px 10px;border-radius:6px;font-weight:700;font-size:12px;margin:2px 0">🎯 $1</span>');
    t = t.replace(/\*\*Uzasadnienie:\s*(.+?)\*\*/g, '<div style="font-size:11px;color:#94a3b8;padding-left:10px;border-left:2px solid rgba(255,255,255,0.06);margin:3px 0">$1</div>');

    // ── Remaining bold/italic
    t = t.replace(/\*\*(.+?)\*\*/g, '<strong style="color:#fff;font-weight:700">$1</strong>');
    t = t.replace(/\*(.+?)\*/g, '<em style="color:#94a3b8">$1</em>');

    // ── Numbered list items → step cards
    t = t.replace(/^(\d+)\.\s+(.+)/gm, (match, num, content) => {
      const colors = ['#00d4ff', '#2ecc71', '#f7b731', '#e74c3c', '#9b59b6', '#3498db'];
      const c = colors[(parseInt(num) - 1) % colors.length];
      return `<div style="display:flex;gap:10px;align-items:flex-start;margin:8px 0;padding:8px 10px;background:rgba(255,255,255,0.02);border-radius:8px">
        <div style="min-width:28px;height:28px;background:${c};border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:13px;color:#0f172a;flex-shrink:0">${num}</div>
        <div style="flex:1;font-size:12px;color:#e2e8f0;line-height:1.5;padding-top:4px">${content}</div>
      </div>`;
    });

    // ── Bullet points
    t = t.replace(/^[\-\*] (.+)/gm, '<div style="display:flex;gap:8px;align-items:flex-start;padding:2px 0 2px 4px"><span style="color:#00d4ff;font-size:8px;margin-top:5px">●</span><span style="font-size:12px;color:#cbd5e1">$1</span></div>');

    // ── Line breaks
    t = t.replace(/\n\n/g, '<div style="margin:8px 0"></div>');
    t = t.replace(/\n/g, '<br>');

    return t;
  }

  _updateHEMSFromAI() {
    const s = this._settings;
    if (s.ai_hems_advice && s.ai_hems_advice.text) {
      const html = this._renderMarkdown(s.ai_hems_advice.text);
      const prov = s.ai_hems_advice.provider || '';
      const ts = s.ai_hems_advice.timestamp || '';
      const content = '<div style="font-size:13px;line-height:1.6">' + html + '</div><div style="font-size:9px;color:#64748b;margin-top:6px">\ud83e\udd16 AI ' + prov + ' \u2022 ' + ts + '</div>';
      // Update overview tab
      const el = this.shadowRoot.getElementById("v-hems-rec");
      if (el) { el.innerHTML = content; this._aiHemsLoaded = true; }
      // Update tariff tab
      const elT = this.shadowRoot.getElementById("v-hems-rec-tariff");
      if (elT) { elT.innerHTML = content; this._aiTariffLoaded = true; }
      // Update HEMS tab
      const elH = this.shadowRoot.getElementById("v-hems-rec-hems");
      if (elH) { elH.innerHTML = content; this._aiHemsTabLoaded = true; }
    }
    if (s.ai_daily_report && s.ai_daily_report.text) {
      // Daily report goes to a separate element if exists
      const html = this._renderMarkdown(s.ai_daily_report.text);
      const prov = s.ai_daily_report.provider || '';
      const ts = s.ai_daily_report.timestamp || '';
      const content = '<div style="font-size:13px;line-height:1.6">' + html + '</div><div style="font-size:9px;color:#64748b;margin-top:6px">\ud83e\udd16 AI ' + prov + ' \u2022 ' + ts + '</div>';
      // If no HEMS advice loaded yet, use daily report as fallback
      if (!this._aiHemsLoaded) {
        const el = this.shadowRoot.getElementById("v-hems-rec");
        if (el) { el.innerHTML = content; this._aiHemsLoaded = true; }
      }
      if (!this._aiTariffLoaded) {
        const elT = this.shadowRoot.getElementById("v-hems-rec-tariff");
        if (elT) { elT.innerHTML = content; this._aiTariffLoaded = true; }
      }
      if (!this._aiHemsTabLoaded) {
        const elH = this.shadowRoot.getElementById("v-hems-rec-hems");
        if (elH) { elH.innerHTML = content; this._aiHemsTabLoaded = true; }
      }
    }
  }
  // ── Winter Tab (Zima na plusie) ──
  // Tariff-based scenario definitions — same energy flows, different prices
  _winterScenarios() {
    // Dynamic tariff prices from ENTSO-E sensors (safe access — _haState may not exist during early render)
    const avgPrice = parseFloat(this._hass?.states?.['sensor.entso_e_srednia_dzisiaj']?.state) || 0.50;
    const minPrice = Math.max(0.05, avgPrice * 0.3);
    const maxPrice = avgPrice * 2.0;

    return {
      none:    { label: '🔴 G11 — Stała cena',
                 importPrice: 1.10, exportPrice: 0.20,
                 desc: 'Taryfa G11: stała cena 1.10 zł/kWh (z opłatami dystrybucji), eksport po RCE min (0.20 zł)' },
      basic:   { label: '🟡 G13 — Strefowa',
                 importPrice: 0.87, exportPrice: 0.35,
                 desc: 'Taryfa G13: off-peak 0.63, poranna 0.91, szczyt 1.50 zł (średnia 0.87), eksport po średniej RCE' },
      optimal: { label: '🟢 Dynamiczna — RCE/ENTSO-E',
                 importPrice: minPrice, exportPrice: maxPrice,
                 desc: `Cena dynamiczna RCE: import w najtańszych godzinach (${minPrice.toFixed(2)} zł), sprzedaż w najdroższych (${maxPrice.toFixed(2)} zł) — pełny arbitraż cenowy HEMS` }
    };
  }

  _initWinterTab() {
    const months = ['Styczeń','Luty','Marzec','Kwiecień','Maj','Czerwiec','Lipiec','Sierpień','Wrzesień','Październik','Listopad','Grudzień'];
    const emojis = ['🥶','🥶','🌨️','🌤️','☀️','☀️','☀️','☀️','🌤️','🍂','🌧️','🥶'];
    const tbody = this.shadowRoot.getElementById('wnt-table-body');
    if (!tbody || tbody.children.length > 0) return;
    const s = this._settings;
    months.forEach((m, i) => {
      const saved = (s.winter_consumption || [])[i] || '';
      const isWinter = [0,1,2,9,10,11].includes(i);
      const tr = document.createElement('tr');
      tr.style.cssText = 'border-bottom:1px solid rgba(255,255,255,0.05);' + (isWinter ? 'background:rgba(0,150,255,0.04)' : '');
      tr.innerHTML = '<td style="padding:5px 6px;color:#cbd5e1;font-size:11px">' + emojis[i] + ' ' + m + '</td>' +
        '<td style="text-align:right;padding:5px 4px"><input type="number" data-month="' + i + '" class="wnt-cons-input" value="' + saved + '" placeholder="—" style="width:70px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:5px;color:#fff;padding:4px 6px;font-size:11px;text-align:right" onchange="this.getRootNode().host._recalcWinter()" /></td>' +
        '<td style="text-align:right;padding:5px 6px;color:#f7b731;font-size:11px" data-pv="' + i + '">—</td>' +
        '<td style="text-align:right;padding:5px 6px;font-weight:600;font-size:11px" data-bal="' + i + '">—</td>' +
        '<td style="text-align:right;padding:5px 6px;font-size:10px;color:#e74c3c" data-cost="' + i + '">—</td>' +
        '<td style="text-align:right;padding:5px 6px;font-size:10px;color:#2ecc71" data-rev="' + i + '">—</td>' +
        '<td style="text-align:right;padding:5px 6px;font-weight:600;font-size:10px" data-fbal="' + i + '">—</td>' +
        '<td style="padding:5px 4px"><div style="background:rgba(255,255,255,0.06);border-radius:4px;height:8px;overflow:hidden"><div data-bar="' + i + '" style="height:100%;width:0%;border-radius:4px;transition:width 0.3s"></div></div></td>';
      tbody.appendChild(tr);
    });
    if (s.winter_pv_kwp) { const k = this.shadowRoot.getElementById('wnt-pv-kwp'); if (k) k.value = s.winter_pv_kwp; }
    if (s.winter_region) { const r = this.shadowRoot.getElementById('wnt-region'); if (r) r.value = s.winter_region; }
    if (s.winter_scenario) { const sc = this.shadowRoot.getElementById('wnt-scenario'); if (sc) sc.value = s.winter_scenario; }
    this._winterLoading = true;
    this._recalcWinter();
    this._winterLoading = false;
  }

  _recalcWinter() {
    const solarDist = {
      south: [0.03,0.04,0.08,0.10,0.13,0.14,0.16,0.13,0.09,0.05,0.03,0.02],
      center:[0.03,0.04,0.08,0.10,0.13,0.14,0.15,0.13,0.09,0.05,0.03,0.03],
      north: [0.02,0.04,0.07,0.10,0.14,0.15,0.16,0.13,0.09,0.05,0.03,0.02]
    };
    const yieldPerKwp = { south: 1050, center: 1000, north: 950 };
    const kwp = parseFloat(this.shadowRoot.getElementById('wnt-pv-kwp')?.value) || 0;
    const region = this.shadowRoot.getElementById('wnt-region')?.value || 'center';
    const scenarioKey = this.shadowRoot.getElementById('wnt-scenario')?.value || 'optimal';
    const scenarios = this._winterScenarios();
    const sc = scenarios[scenarioKey] || scenarios.optimal;
    const annualYield = kwp * (yieldPerKwp[region] || 1000);
    const dist = solarDist[region] || solarDist.center;
    const estEl = this.shadowRoot.getElementById('wnt-est-yearly');
    if (estEl) estEl.textContent = kwp > 0 ? Math.round(annualYield) + ' kWh' : '— kWh';
    const inputs = this.shadowRoot.querySelectorAll('.wnt-cons-input');
    let totalCons = 0, totalPV = 0, totalCost = 0, totalRev = 0;
    const monthData = [];
    inputs.forEach((inp, i) => {
      const cons = parseFloat(inp.value) || 0;
      const pv = kwp > 0 ? Math.round(annualYield * dist[i]) : 0;
      const bal = pv - cons;
      const cost = bal < 0 ? Math.abs(bal) * sc.importPrice : 0;
      const rev = bal > 0 ? bal * sc.exportPrice : 0;
      const fbal = rev - cost;
      totalCons += cons; totalPV += pv; totalCost += cost; totalRev += rev;
      monthData.push({ cons, pv, bal, cost, rev, fbal, month: i });
      const pvC = this.shadowRoot.querySelector('[data-pv="' + i + '"]');
      const balC = this.shadowRoot.querySelector('[data-bal="' + i + '"]');
      const costC = this.shadowRoot.querySelector('[data-cost="' + i + '"]');
      const revC = this.shadowRoot.querySelector('[data-rev="' + i + '"]');
      const fbalC = this.shadowRoot.querySelector('[data-fbal="' + i + '"]');
      const barE = this.shadowRoot.querySelector('[data-bar="' + i + '"]');
      if (pvC) pvC.textContent = pv > 0 ? pv : '—';
      if (balC) { balC.textContent = cons > 0 ? (bal > 0 ? '+' + bal : '' + bal) : '—'; balC.style.color = bal >= 0 ? '#2ecc71' : '#e74c3c'; }
      if (costC) costC.textContent = cost > 0 ? '-' + cost.toFixed(0) : '—';
      if (revC) revC.textContent = rev > 0 ? '+' + rev.toFixed(0) : '—';
      if (fbalC) { fbalC.textContent = cons > 0 ? (fbal >= 0 ? '+' : '') + fbal.toFixed(0) + ' zł' : '—'; fbalC.style.color = fbal >= 0 ? '#2ecc71' : '#e74c3c'; }
      if (barE && cons > 0) { const p = Math.min((pv / Math.max(cons, 1)) * 100, 100); barE.style.width = p + '%'; barE.style.background = p >= 100 ? '#2ecc71' : p >= 60 ? '#f7b731' : '#e74c3c'; }
    });
    const totalBal = totalPV - totalCons;
    const totalFBal = totalRev - totalCost;
    this._setText('wnt-sum-cons', totalCons > 0 ? totalCons + ' kWh' : '—');
    this._setText('wnt-sum-pv', totalPV > 0 ? totalPV + ' kWh' : '—');
    const sBal = this.shadowRoot.getElementById('wnt-sum-bal');
    if (sBal) { sBal.textContent = totalCons > 0 ? (totalBal > 0 ? '+' : '') + totalBal + ' kWh' : '—'; sBal.style.color = totalBal >= 0 ? '#2ecc71' : '#e74c3c'; }
    this._setText('wnt-sum-cost', totalCost > 0 ? '-' + totalCost.toFixed(0) + ' zł' : '—');
    this._setText('wnt-sum-rev', totalRev > 0 ? '+' + totalRev.toFixed(0) + ' zł' : '—');
    const sfb = this.shadowRoot.getElementById('wnt-sum-fbal');
    if (sfb) { sfb.textContent = totalCons > 0 ? (totalFBal >= 0 ? '+' : '') + totalFBal.toFixed(0) + ' zł' : '—'; sfb.style.color = totalFBal >= 0 ? '#2ecc71' : '#e74c3c'; }
    this._setText('wnt-total-consumption', totalCons > 0 ? totalCons + ' kWh' : '— kWh');
    this._setText('wnt-total-production', totalPV > 0 ? totalPV + ' kWh' : '— kWh');
    const sn = this.shadowRoot.getElementById('wnt-balance-sign'); if (sn) sn.textContent = totalBal >= 0 ? '✅' : '⚠️';
    const bx = this.shadowRoot.getElementById('wnt-balance-box');
    const vl = this.shadowRoot.getElementById('wnt-balance-value');
    const mg = this.shadowRoot.getElementById('wnt-balance-msg');
    if (totalCons > 0 && kwp > 0) {
      if (vl) { vl.textContent = (totalBal > 0 ? '+' : '') + totalBal + ' kWh'; vl.style.color = totalBal >= 0 ? '#2ecc71' : '#e74c3c'; }
      if (bx) bx.style.background = totalBal >= 0 ? 'rgba(46,204,113,0.1)' : 'rgba(231,76,60,0.1)';
      if (mg) { mg.textContent = totalBal >= 0 ? 'Nadwyżka: ' + totalBal + ' kWh/rok — JESTEŚ NA PLUSIE!' : 'Niedobór: ' + Math.abs(totalBal) + ' kWh/rok'; mg.style.color = totalBal >= 0 ? '#2ecc71' : '#f7b731'; }
    }
    // Financial balance in hero
    const fvl = this.shadowRoot.getElementById('wnt-fbal-value');
    const fmg = this.shadowRoot.getElementById('wnt-fbal-msg');
    if (totalCons > 0 && kwp > 0 && fvl) {
      fvl.textContent = (totalFBal >= 0 ? '+' : '') + totalFBal.toFixed(0) + ' zł';
      fvl.style.color = totalFBal >= 0 ? '#2ecc71' : '#e74c3c';
      if (fmg) { fmg.textContent = 'Scenariusz: ' + sc.label; fmg.style.color = '#94a3b8'; }
    }
    const cov = totalCons > 0 && totalPV > 0 ? Math.round(totalPV / totalCons * 100) : 0;
    const cb = this.shadowRoot.getElementById('wnt-coverage-bar'); if (cb) cb.style.width = Math.min(cov, 120) + '%';
    const se = this.shadowRoot.getElementById('wnt-status-emoji');
    const st = this.shadowRoot.getElementById('wnt-status-text');
    const sd = this.shadowRoot.getElementById('wnt-status-desc');
    if (cov === 0) { if (se) se.textContent = '❓'; if (st) st.textContent = 'Brak danych'; }
    else if (cov >= 110) { if (se) se.textContent = '🏆'; if (st) { st.textContent = 'DOSKONALE!'; st.style.color = '#2ecc71'; } if (sd) sd.textContent = 'Pokrycie ' + cov + '% — nadwyżka!'; }
    else if (cov >= 100) { if (se) se.textContent = '✅'; if (st) { st.textContent = 'NA PLUSIE!'; st.style.color = '#2ecc71'; } if (sd) sd.textContent = 'Pokrycie ' + cov + '%!'; }
    else if (cov >= 80) { if (se) se.textContent = '👍'; if (st) { st.textContent = 'Prawie na plusie'; st.style.color = '#f7b731'; } if (sd) sd.textContent = 'Pokrycie ' + cov + '% — brakuje ' + (100-cov) + '%'; }
    else if (cov >= 50) { if (se) se.textContent = '⚡'; if (st) { st.textContent = 'Wymaga uwagi'; st.style.color = '#e67e22'; } if (sd) sd.textContent = 'Pokrycie ' + cov + '%'; }
    else { if (se) se.textContent = '🥶'; if (st) { st.textContent = 'Daleko od celu'; st.style.color = '#e74c3c'; } if (sd) sd.textContent = 'Pokrycie ' + cov + '%'; }
    this._renderWinterChart(monthData);
    this._renderWinterFocus(monthData);
    this._renderScenarioComparison(monthData);
    // Auto-save winter data with debounce (1.5s) — skip during initial load
    if (!this._winterLoading) {
      if (this._winterSaveTimeout) clearTimeout(this._winterSaveTimeout);
      this._winterSaveTimeout = setTimeout(() => this._saveWinterData(), 1500);
    }
  }

  _renderScenarioComparison(data) {
    const ct = this.shadowRoot.getElementById('wnt-scenario-compare');
    if (!ct || data.every(d => d.cons === 0)) { if (ct) ct.innerHTML = '<div style="color:#64748b;font-size:11px;text-align:center;padding:16px">Wypełnij dane zużycia aby zobaczyć porównanie scenariuszy.</div>'; return; }
    const scenarios = this._winterScenarios();
    const keys = ['none', 'basic', 'optimal'];
    const colors = ['#e74c3c', '#f7b731', '#2ecc71'];
    const borders = ['rgba(231,76,60,0.2)', 'rgba(247,183,49,0.2)', 'rgba(46,204,113,0.2)'];
    const bgs = ['rgba(231,76,60,0.06)', 'rgba(247,183,49,0.06)', 'rgba(46,204,113,0.06)'];
    let html = '';
    const results = keys.map(k => {
      const s = scenarios[k];
      let cost = 0, rev = 0;
      data.forEach(d => {
        if (d.bal < 0) cost += Math.abs(d.bal) * s.importPrice;
        if (d.bal > 0) rev += d.bal * s.exportPrice;
      });
      return { key: k, label: s.label, desc: s.desc, cost, rev, net: rev - cost, importPrice: s.importPrice, exportPrice: s.exportPrice };
    });
    const bestNet = results[2].net;
    const worstNet = results[0].net;
    const savings = bestNet - worstNet;
    html = results.map((r, i) => {
      const diff = i > 0 ? r.net - worstNet : 0;
      return '<div style="flex:1;background:' + bgs[i] + ';border:1px solid ' + borders[i] + ';border-radius:12px;padding:14px;text-align:center;min-width:180px">' +
        '<div style="font-size:12px;font-weight:700;color:' + colors[i] + ';margin-bottom:8px">' + r.label + '</div>' +
        '<div style="font-size:9px;color:#64748b;margin-bottom:10px;line-height:1.3">' + r.desc + '</div>' +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-bottom:8px;font-size:10px">' +
        '<div style="color:#64748b">Import:</div><div style="color:#e74c3c;font-weight:600">' + r.importPrice.toFixed(2) + ' zł/kWh</div>' +
        '<div style="color:#64748b">Eksport:</div><div style="color:#2ecc71;font-weight:600">' + r.exportPrice.toFixed(2) + ' zł/kWh</div>' +
        '</div>' +
        '<div style="border-top:1px solid rgba(255,255,255,0.06);padding-top:8px">' +
        '<div style="font-size:9px;color:#64748b">Koszt importu</div><div style="font-size:14px;font-weight:700;color:#e74c3c">-' + r.cost.toFixed(0) + ' zł</div>' +
        '<div style="font-size:9px;color:#64748b;margin-top:4px">Przychód z eksportu</div><div style="font-size:14px;font-weight:700;color:#2ecc71">+' + r.rev.toFixed(0) + ' zł</div>' +
        '</div>' +
        '<div style="margin-top:8px;padding:8px;border-radius:8px;background:rgba(255,255,255,0.04)">' +
        '<div style="font-size:9px;color:#64748b">BILANS ROCZNY</div>' +
        '<div style="font-size:22px;font-weight:900;color:' + (r.net >= 0 ? '#2ecc71' : '#e74c3c') + '">' + (r.net >= 0 ? '+' : '') + r.net.toFixed(0) + ' zł</div>' +
        (i > 0 ? '<div style="font-size:10px;color:#2ecc71;margin-top:2px">+' + diff.toFixed(0) + ' zł vs G11</div>' : '<div style="font-size:10px;color:#e74c3c;margin-top:2px">taryfa stała</div>') +
        '</div></div>';
    }).join('');
    ct.innerHTML = '<div style="display:flex;gap:10px;flex-wrap:wrap">' + html + '</div>' +
      (savings > 0 ? '<div style="margin-top:12px;padding:12px;background:rgba(46,204,113,0.08);border:1px solid rgba(46,204,113,0.2);border-radius:10px;text-align:center">' +
      '<div style="font-size:11px;color:#94a3b8">💰 Różnica cenowa: Dynamiczna RCE vs G11</div>' +
      '<div style="font-size:28px;font-weight:900;color:#2ecc71;margin-top:4px">+' + savings.toFixed(0) + ' zł/rok</div>' +
      '<div style="font-size:10px;color:#64748b;margin-top:2px">Oszczędność dzięki taryfie dynamicznej vs stała cena G11</div></div>' : '');
  }

  _renderWinterChart(data) {
    const ch = this.shadowRoot.getElementById('wnt-chart'); if (!ch) return;
    const mx = Math.max(...data.map(d => Math.max(d.cons, d.pv)), 1);
    const lb = ['Sty','Lut','Mar','Kwi','Maj','Cze','Lip','Sie','Wrz','Paź','Lis','Gru'];
    ch.innerHTML = data.map((d, i) => {
      const cH = d.cons > 0 ? Math.max((d.cons/mx)*160, 4) : 0;
      const pH = d.pv > 0 ? Math.max((d.pv/mx)*160, 4) : 0;
      return '<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:2px">' +
        '<div style="display:flex;gap:2px;align-items:flex-end;height:160px">' +
        '<div style="width:8px;height:' + cH + 'px;background:#e74c3c;border-radius:3px 3px 0 0;opacity:0.8" title="Zużycie: ' + d.cons + ' kWh"></div>' +
        '<div style="width:8px;height:' + pH + 'px;background:#f7b731;border-radius:3px 3px 0 0;opacity:0.8" title="PV: ' + d.pv + ' kWh"></div>' +
        '</div><div style="font-size:8px;color:#64748b">' + lb[i] + '</div></div>';
    }).join('');
  }

  _renderWinterFocus(data) {
    const ct = this.shadowRoot.getElementById('wnt-winter-cards');
    const sg = this.shadowRoot.getElementById('wnt-suggestions');
    if (!ct) return;
    const wm = [9,10,11,0,1,2];
    const wl = ['Październik','Listopad','Grudzień','Styczeń','Luty','Marzec'];
    let deficit = 0; const tips = [];
    ct.innerHTML = wm.map((mi, idx) => {
      const d = data[mi]; const b = d.bal;
      if (d.cons > 0) deficit += Math.max(-b, 0);
      const c = b >= 0 ? '#2ecc71' : '#e74c3c';
      return '<div style="background:' + (b >= 0 ? 'rgba(46,204,113,0.08)' : 'rgba(231,76,60,0.08)') + ';border:1px solid ' + c + '22;border-radius:10px;padding:10px;text-align:center">' +
        '<div style="font-size:10px;color:#94a3b8">' + wl[idx] + '</div>' +
        '<div style="font-size:18px;font-weight:800;color:' + c + ';margin-top:2px">' + (d.cons > 0 ? (b > 0 ? '+' + b : '' + b) : '—') + '</div>' +
        '<div style="font-size:9px;color:#64748b">kWh</div>' +
        '<div style="font-size:9px;color:' + c + ';margin-top:2px">' + (d.cons > 0 ? (b >= 0 ? 'nadwyżka' : 'niedobór') : '—') + '</div></div>';
    }).join('');
    if (deficit > 0) {
      tips.push('🔴 Łączny niedobór zimowy (X-III): <strong>' + Math.round(deficit) + ' kWh</strong>');
      tips.push('💡 Magazyn energii min. <strong>' + Math.round(deficit/180) + ' kWh</strong> pokryje deficyt');
      tips.push('☀️ Lub zwiększ PV o <strong>' + (Math.round(deficit/200*10)/10) + ' kWp</strong>');
      tips.push('🏠 LED, pompa ciepła COP 4+, izolacja — kluczowe zimą');
      tips.push(`💰 Taryfa ${this._settings.tariff_plan || 'G13'} — ładuj baterię w nocy (off-peak), zużywaj w szczycie`);
    } else if (data.some(d => d.cons > 0)) {
      tips.push('🏆 <strong>Brawo!</strong> Instalacja pokrywa zimowe zapotrzebowanie!');
      tips.push('💰 Sprzedawaj nadwyżki po korzystnych cenach RCE');
      tips.push('🔋 Magazyn energii = arbitraż cenowy + niezależność');
    }
    if (sg) sg.innerHTML = tips.length > 0 ? tips.join('<br>') : 'Wypełnij dane aby zobaczyć sugestie.';
  }

  _saveWinterData() {
    const inputs = this.shadowRoot.querySelectorAll('.wnt-cons-input');
    const cons = []; inputs.forEach(inp => cons.push(parseFloat(inp.value) || 0));
    this._savePanelSettings({
      winter_consumption: cons,
      winter_pv_kwp: parseFloat(this.shadowRoot.getElementById('wnt-pv-kwp')?.value) || 0,
      winter_region: this.shadowRoot.getElementById('wnt-region')?.value || 'center',
      winter_scenario: this.shadowRoot.getElementById('wnt-scenario')?.value || 'optimal'
    });
    const st = this.shadowRoot.getElementById('wnt-save-status');
    if (st) { st.textContent = '\u2705 Dane zimowe zapisane!'; setTimeout(() => { st.textContent = ''; }, 4000); }
  }

  _loadWinterData() {
    this._winterLoading = true;
    const s = this._settings;
    if (s.winter_consumption) { const ii = this.shadowRoot.querySelectorAll('.wnt-cons-input'); ii.forEach((inp, i) => { if (s.winter_consumption[i]) inp.value = s.winter_consumption[i]; }); }
    if (s.winter_pv_kwp) { const k = this.shadowRoot.getElementById('wnt-pv-kwp'); if (k) k.value = s.winter_pv_kwp; }
    if (s.winter_region) { const r = this.shadowRoot.getElementById('wnt-region'); if (r) r.value = s.winter_region; }
    if (s.winter_scenario) { const sc = this.shadowRoot.getElementById('wnt-scenario'); if (sc) sc.value = s.winter_scenario; }
    this._recalcWinter();
    this._winterLoading = false;
  }

  async _uploadInverterImage(file) {
    if (!file) return;
    const st = this.shadowRoot.getElementById("v-upload-status");
    const preview = this.shadowRoot.getElementById("upload-preview");
    const sizeInfo = this.shadowRoot.getElementById("upload-size-info");
    if (st) st.textContent = "⏳ Wgrywanie...";
    // Show file info
    const sizeMB = (file.size / 1024 / 1024).toFixed(2);
    if (sizeInfo) sizeInfo.textContent = `📄 ${file.name} • ${sizeMB} MB • ${file.type}`;
    try {
      const reader = new FileReader();
      reader.onload = () => {
        // Show preview
        if (preview) {
          preview.src = reader.result;
          preview.style.display = 'block';
        }
        if (this._hass) {
          this._hass.callService("smartinghome", "upload_inverter_image", {
            filename: file.name,
            data: reader.result.split(",")[1],
          });
          if (st) {
            st.innerHTML = '✅ Zdjęcie wgrane! <span style="color:#94a3b8">Odśwież panel (Ctrl+Shift+R) aby zobaczyć w Przeglądzie.</span>';
          }
          // Update overview image immediately
          const invImg = this.shadowRoot.getElementById("v-inv-img");
          if (invImg) {
            invImg.src = `/local/smartinghome/inverter.png?t=${Date.now()}`;
            invImg.style.display = 'block';
            const iconEl = this.shadowRoot.getElementById("v-inv-icon");
            if (iconEl) iconEl.style.display = 'none';
          }
          this._settings.custom_inverter_image = true;
          this._savePanelSettings({ custom_inverter_image: true });
        }
      };
      reader.readAsDataURL(file);
    } catch (e) {
      if (st) st.textContent = "❌ Błąd wgrywania: " + e.message;
    }
  }

  _uploadHomeImage(file) {
    if (!file) return;
    const preview = this.shadowRoot.getElementById('home-upload-preview');
    const st = this.shadowRoot.getElementById('v-home-upload-status');
    try {
      const reader = new FileReader();
      reader.onload = () => {
        if (preview) { preview.src = reader.result; preview.style.display = 'block'; }
        if (this._hass) {
          this._hass.callService("smartinghome", "upload_inverter_image", {
            filename: "home.png",
            data: reader.result.split(",")[1],
          });
          if (st) { st.innerHTML = '✅ Zdjęcie domu wgrane! <span style="color:#94a3b8">Odśwież panel.</span>'; }
          const homeImg = this.shadowRoot.getElementById("v-home-img");
          if (homeImg) { homeImg.src = `/local/smartinghome/home.png?t=${Date.now()}`; homeImg.style.display = 'block'; }
          this._settings.custom_home_image = true;
          this._savePanelSettings({ custom_home_image: true });
        }
      };
      reader.readAsDataURL(file);
    } catch (e) {
      if (st) st.textContent = "❌ Błąd wgrywania: " + e.message;
    }
  }

  _updateKeyStatus() {
    ["gemini", "anthropic"].forEach(p => {
      const ind = this.shadowRoot.getElementById(`key-status-${p}`);
      if (!ind) return;
      const s = this._settings[`${p}_key_status`];
      if (s === "valid") { ind.textContent = "✅ Klucz zweryfikowany"; ind.style.color = "#2ecc71"; }
      else if (s === "invalid") { ind.textContent = "❌ Klucz nieprawidłowy"; ind.style.color = "#e74c3c"; }
      else if (s === "saved") { ind.textContent = "💾 Klucz zapisany (niesprawdzony)"; ind.style.color = "#f39c12"; }
      else { ind.textContent = "— Brak klucza"; ind.style.color = "#64748b"; }
    });
  }

  async _testApiKey(provider) {
    const btn = this.shadowRoot.getElementById(`test-btn-${provider}`);
    if (btn) { btn.textContent = "⏳ Testowanie..."; btn.disabled = true; }
    if (!this._testSub) {
      this._testSub = this._hass.connection.subscribeEvents((ev) => {
        const d = ev.data;
        this._settings[`${d.provider}_key_status`] = d.status;
        this._updateKeyStatus();
        const b = this.shadowRoot.getElementById(`test-btn-${d.provider}`);
        if (b) { b.textContent = "🧪 Testuj"; b.disabled = false; }
        // Persist verification status to settings.json so it survives restart
        if (d.status === "valid") {
          this._savePanelSettings();
        }
      }, "smartinghome_api_key_test");
    }
    setTimeout(() => { if (btn) { btn.textContent = "🧪 Testuj"; btn.disabled = false; } }, 15000);
    // Test the key stored on backend (managed via HA integration options)
    if (this._hass) {
      this._hass.callService("smartinghome", "test_api_key", { provider, api_key: "" });
    }
  }

  // Smart unit formatting
  _pw(w) {
    if (w === null || isNaN(w)) return "—";
    return Math.abs(w) >= 1000 ? `${(w/1000).toFixed(1)} kW` : `${Math.round(w)} W`;
  }

  /* ── Subscription management (reconnection-safe) ── */
  _ensureSubscriptions() {
    if (!this._hass?.connection) return;
    const conn = this._hass.connection;
    // Detect connection change → clear stale subscriptions
    if (this._lastConnection && this._lastConnection !== conn) {
      console.log('[SH] Connection changed, resubscribing...');
      if (this._cronSub) { try { this._cronSub(); } catch(e) {} this._cronSub = null; }
      if (this._actionStateSub) { try { this._actionStateSub(); } catch(e) {} this._actionStateSub = null; }
      if (this._testSub) { try { this._testSub(); } catch(e) {} this._testSub = null; }
    }
    this._lastConnection = conn;
    // Subscribe to AI cron updates
    if (!this._cronSub) {
      try {
        this._cronSub = conn.subscribeEvents((ev) => {
          const d = ev.data;
          if (d.result_key) this._settings[d.result_key] = { text: d.text, timestamp: d.timestamp, provider: d.provider };
          this._updateHEMSFromAI();
          this._updateCronStatus();
          this._renderAILogs();
        }, "smartinghome_ai_cron_update");
        // subscribeEvents returns a Promise<unsubscribe> — unwrap it
        if (this._cronSub && typeof this._cronSub.then === 'function') {
          this._cronSub.then(unsub => { this._cronSub = unsub; }).catch(err => {
            console.warn('[SH] Failed to subscribe to cron updates:', err);
            this._cronSub = null;
          });
        }
      } catch(e) { console.warn('[SH] subscribeEvents cron error:', e); this._cronSub = null; }
    }
    // Subscribe to action state updates
    if (!this._actionStateSub) {
      try {
        this._actionStateSub = conn.subscribeEvents((ev) => {
          this._actionStates = ev.data || {};
          this._updateActionBadgesFromState();
        }, "smartinghome_action_states");
        if (this._actionStateSub && typeof this._actionStateSub.then === 'function') {
          this._actionStateSub.then(unsub => { this._actionStateSub = unsub; }).catch(err => {
            console.warn('[SH] Failed to subscribe to action states:', err);
            this._actionStateSub = null;
          });
        }
      } catch(e) { console.warn('[SH] subscribeEvents action error:', e); this._actionStateSub = null; }
    }
  }

  /* ── Update all ─────────────────────────── */
  _updateAll() { this._updateFlow(); this._updateStats(); this._updateHomeImage(); this._updateG13Timeline(); this._updateSunWidget(); this._renderWeatherForecast(); this._updateEcowittCard(); this._calcHEMSScore(); this._updateWindTab(); this._updateHEMSArbitrage(); this._updateHistoryTab(); this._updateAutopilotVisibility(); this._updateSubMeters(); this._updateSubMetersInCard(); }

  /* ── Moon phase calculation ─────────────────── */
  _getMoonPhase(date) {
    // Algorithm based on synodic month (29.53058770576 days)
    // Reference new moon: January 6, 2000 18:14 UTC
    const refNewMoon = new Date(Date.UTC(2000, 0, 6, 18, 14, 0));
    const synodicMonth = 29.53058770576;
    const daysSinceRef = (date.getTime() - refNewMoon.getTime()) / 86400000;
    const moonAge = ((daysSinceRef % synodicMonth) + synodicMonth) % synodicMonth;
    const phase = moonAge / synodicMonth; // 0..1

    // Moon phase names and emojis
    // Phase ranges (approximate):
    // 0.000 - 0.025: New Moon 🌑
    // 0.025 - 0.225: Waxing Crescent 🌒
    // 0.225 - 0.275: First Quarter 🌓
    // 0.275 - 0.475: Waxing Gibbous 🌔
    // 0.475 - 0.525: Full Moon 🌕
    // 0.525 - 0.725: Waning Gibbous 🌖
    // 0.725 - 0.775: Last Quarter 🌗
    // 0.775 - 0.975: Waning Crescent 🌘
    // 0.975 - 1.000: New Moon 🌑
    let emoji, name;
    if (phase < 0.025 || phase >= 0.975) { emoji = '🌑'; name = 'Nów'; }
    else if (phase < 0.225) { emoji = '🌒'; name = 'Przybywający sierp'; }
    else if (phase < 0.275) { emoji = '🌓'; name = 'Pierwsza kwadra'; }
    else if (phase < 0.475) { emoji = '🌔'; name = 'Przybywający garb'; }
    else if (phase < 0.525) { emoji = '🌕'; name = 'Pełnia'; }
    else if (phase < 0.725) { emoji = '🌖'; name = 'Ubywający garb'; }
    else if (phase < 0.775) { emoji = '🌗'; name = 'Ostatnia kwadra'; }
    else { emoji = '🌘'; name = 'Ubywający sierp'; }

    return { phase, moonAge: moonAge.toFixed(1), emoji, name };
  }

  _updateSunWidget() {
    const now = new Date();
    const dayNames = ['Niedziela','Poniedziałek','Wtorek','Środa','Czwartek','Piątek','Sobota'];
    const monthNames = ['stycznia','lutego','marca','kwietnia','maja','czerwca','lipca','sierpnia','września','października','listopada','grudnia'];

    // Clock
    this._setText("ov-clock", `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`);
    this._setText("ov-date", `${now.getDate()} ${monthNames[now.getMonth()]} ${now.getFullYear()}`);
    this._setText("ov-day-name", dayNames[now.getDay()]);

    // Sun data from HA sun entity
    const sunState = this._hass?.states?.["sun.sun"];
    if (!sunState) return;
    const attrs = sunState.attributes || {};
    const isDay = sunState.state === "above_horizon";
    const nextRising = attrs.next_rising ? new Date(attrs.next_rising) : null;
    const nextSetting = attrs.next_setting ? new Date(attrs.next_setting) : null;
    if (!nextRising || !nextSetting) return;

    // Calculate today's sunrise/sunset
    let todaySunrise, todaySunset;
    if (isDay) {
      // During day: next_setting = today's sunset, next_rising = tomorrow's sunrise
      todaySunset = nextSetting;
      todaySunrise = new Date(nextRising.getTime() - 86400000); // yesterday's scheme → today's
    } else {
      // At night: next_rising = tomorrow's sunrise, next_setting = tomorrow's sunset
      todaySunrise = nextRising;
      todaySunset = nextSetting;
    }

    const fmt = (d) => `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;

    if (isDay) {
      // ☀️ DAYTIME
      this._setText("ov-sunrise", fmt(todaySunrise));
      this._setText("ov-sunset", fmt(todaySunset));

      const dayLen = todaySunset.getTime() - todaySunrise.getTime();
      const elapsed = now.getTime() - todaySunrise.getTime();
      const t = Math.max(0, Math.min(1, elapsed / dayLen));

      this._setText("ov-daylight-pct", `${Math.round(t * 100)}%`);
      const msLeft = todaySunset.getTime() - now.getTime();
      const hLeft = Math.floor(msLeft / 3600000);
      const mLeft = Math.floor((msLeft % 3600000) / 60000);
      this._setText("ov-daylight-left", `${hLeft}h ${mLeft}m do zachodu`);
      const statusLabel = this.shadowRoot.getElementById("ov-status-label");
      if (statusLabel) { statusLabel.textContent = "☀️ Dzień"; statusLabel.style.color = "#f7b731"; }

      // Arc is now handled by clip-path in sync with the dot
      const arcPath = this.shadowRoot.getElementById("ov-sun-arc");
      if (arcPath) {
        arcPath.setAttribute("stroke", "#f7b731");
        arcPath.setAttribute("stroke-width", "2.5");
        arcPath.removeAttribute("stroke-dasharray");
      }
      // Hide moon phase name during day
      const moonPhaseEl = this.shadowRoot.getElementById("ov-moon-phase-name");
      if (moonPhaseEl) moonPhaseEl.style.display = 'none';

      const dot = this.shadowRoot.getElementById("ov-sun-dot");
      if (dot) {
        const p0 = {x:10, y:98}, p1 = {x:100, y:-15}, p2 = {x:190, y:98};
        const cx = (1-t)*(1-t)*p0.x + 2*(1-t)*t*p1.x + t*t*p2.x;
        const cy = (1-t)*(1-t)*p0.y + 2*(1-t)*t*p1.y + t*t*p2.y;
        dot.setAttribute("cx", String(cx));
        dot.setAttribute("cy", String(cy));
        dot.setAttribute("fill", "#f7b731");
        dot.setAttribute("r", "7");
        dot.style.filter = "drop-shadow(0 0 8px #f7b731)";
        const clipRect = this.shadowRoot.getElementById("ov-sun-clip-rect");
        if (clipRect) clipRect.setAttribute("width", String(cx));
      }
    } else {
      // 🌙 NIGHTTIME
      this._setText("ov-sunrise", fmt(todaySunrise));
      this._setText("ov-sunset", fmt(todaySunset));

      const msToRise = todaySunrise.getTime() - now.getTime();
      const hToRise = Math.floor(msToRise / 3600000);
      const mToRise = Math.floor((msToRise % 3600000) / 60000);

      // Moon phase
      const moon = this._getMoonPhase(now);
      this._setText("ov-daylight-pct", moon.emoji);
      this._setText("ov-daylight-left", `${hToRise}h ${mToRise}m do wschodu`);
      const statusLabel = this.shadowRoot.getElementById("ov-status-label");
      if (statusLabel) { statusLabel.textContent = "🌙 Noc"; statusLabel.style.color = "#8b9dc3"; }

      // Moon phase name
      const moonPhaseEl = this.shadowRoot.getElementById("ov-moon-phase-name");
      if (moonPhaseEl) { moonPhaseEl.textContent = moon.name; moonPhaseEl.style.display = ''; }

      // Night arc progress: compute how far through the night we are
      // Night started at todaySunset of previous day, ends at todaySunrise
      // For simplicity: total night = 24h - dayLength, elapsed = time since sunset
      let nightStart;
      if (nextSetting && nextSetting > nextRising) {
        // next_setting is tomorrow's sunset → yesterday's sunset was ~24h ago
        nightStart = new Date(nextSetting.getTime() - 86400000);
      } else {
        nightStart = todaySunset;
      }
      const nightLen = todaySunrise.getTime() - nightStart.getTime();
      const nightElapsed = now.getTime() - nightStart.getTime();
      const tNight = Math.max(0, Math.min(1, nightElapsed / nightLen));

      // Moon arc: show progress with inverted arc (moon travels across sky)
      const arcPath = this.shadowRoot.getElementById("ov-sun-arc");
      if (arcPath) {
        arcPath.setAttribute("stroke", "#8b9dc3");
        arcPath.setAttribute("stroke-width", "1.5");
        arcPath.setAttribute("stroke-dasharray", "4,4");
      }
      const clipRect = this.shadowRoot.getElementById("ov-sun-clip-rect");

      // Move moon dot along the arc
      const dot = this.shadowRoot.getElementById("ov-sun-dot");
      if (dot) {
        const p0 = {x:10, y:98}, p1 = {x:100, y:-15}, p2 = {x:190, y:98};
        const cx = (1-tNight)*(1-tNight)*p0.x + 2*(1-tNight)*tNight*p1.x + tNight*tNight*p2.x;
        const cy = (1-tNight)*(1-tNight)*p0.y + 2*(1-tNight)*tNight*p1.y + tNight*tNight*p2.y;
        dot.setAttribute("cx", String(cx));
        dot.setAttribute("cy", String(cy));
        dot.setAttribute("fill", "#c4d4e8");
        dot.setAttribute("r", "6");
        dot.style.filter = "drop-shadow(0 0 6px rgba(139,157,195,0.6))";
        if (clipRect) clipRect.setAttribute("width", String(cx));
      }
    }
  }

  _updateFlow() {
    const pv = this._nm("pv_power") || 0;
    const load = this._nm("load_power") || 0;
    const grid = -1 * (this._nm("grid_power") || 0);  // GoodWe: +export/-import → invert to +import/-export
    const batt = this._nm("battery_power") || 0;
    const soc = this._nm("battery_soc") || 0;

    // PV
    this._setText("v-pv", this._pw(pv));
    this._setText("v-pv-today", `${this._fm("pv_today")} kWh`);
    // PV total kWh summary
    const pvTotalKwhEl = this.shadowRoot.getElementById("v-pv-total-kwh");
    const pvTodayVal = this._nm("pv_today");
    if (pvTotalKwhEl && pvTodayVal !== null) {
      pvTotalKwhEl.textContent = `⚡ Łączna produkcja dziś: ${pvTodayVal.toFixed(1)} kWh`;
    }
    // Dynamic PV node coloring: green if PV >= load, orange if not
    const pvNode = this.shadowRoot.getElementById("pv-node");
    const pvBig = this.shadowRoot.getElementById("v-pv");
    if (pvNode) {
      const pvCoversLoad = pv >= load && pv > 10;
      const pvColor = pvCoversLoad ? "rgba(46,204,113," : "rgba(247,183,49,";
      pvNode.style.borderColor = pvColor + "0.5)";
      pvNode.style.boxShadow = `0 8px 32px 0 ${pvColor}0.2)`;
      if (pvBig) pvBig.style.color = pvCoversLoad ? "#2ecc71" : "#f7b731";
    }
    // PV Strings
    const pvLabels = (this._settings.pv_labels) || {};
    for (let i = 1; i <= 4; i++) {
      const p = this._nm(`pv${i}_power`);
      const box = this.shadowRoot.getElementById(`pv${i}-box`);
      if (box) box.style.display = p !== null && p > 0 ? "block" : (i <= 2 ? "block" : "none");
      this._setText(`v-pv${i}-p`, p !== null ? this._pw(p) : "—");
      this._setText(`v-pv${i}-v`, `${this._fm(`pv${i}_voltage`)} V`);
      this._setText(`v-pv${i}-a`, `${this._fm(`pv${i}_current`)} A`);
      // Per-string kWh estimate
      const kwhEl = this.shadowRoot.getElementById(`v-pv${i}-kwh`);
      if (kwhEl && pvTodayVal !== null && p !== null && pv > 0) {
        const ratio = p / (pv || 1);
        kwhEl.textContent = `↑ ${(pvTodayVal * ratio).toFixed(1)} kWh`;
      }
      // Custom label — update only the text span, preserve ⚙️ button
      const labelTextEl = this.shadowRoot.getElementById(`pv${i}-label-text`);
      if (labelTextEl && pvLabels[`pv${i}`]) labelTextEl.textContent = pvLabels[`pv${i}`];
    }
    // Render sub-string boxes (proportional split)
    this._renderSubstringBoxes();

    // Home / Load
    this._setText("v-load", this._pw(load));
    // Load daily kWh
    const loadTodayEl = this.shadowRoot.getElementById("v-load-today");
    const loadTodayVal = this._nm("load_today") || this._s("sensor.today_load") || null;
    if (loadTodayEl) {
      const val = typeof loadTodayVal === 'number' ? loadTodayVal : parseFloat(loadTodayVal);
      if (!isNaN(val)) loadTodayEl.textContent = `📊 Dziś: ${val.toFixed(1)} kWh`;
    }
    this._setText("v-load-l1", `L1: ${this._fm("power_l1", 0)} W`);
    this._setText("v-load-l2", `L2: ${this._fm("power_l2", 0)} W`);
    this._setText("v-load-l3", `L3: ${this._fm("power_l3", 0)} W`);

    // ── Day/Night energy accumulation ──
    if (!this._settingsLoaded) {
      // Wait for settings to load before restoring/accumulating — show placeholders
      this._setText("v-load-day", "—");
      this._setText("v-load-night", "—");
      this._setText("v-load-from-pv", "— kWh");
      this._setText("v-load-from-pv-pct", "");
    } else {
      const sunState = this._hass?.states?.["sun.sun"];
      const isDay = sunState?.state === "above_horizon";
      const nowTs = Date.now();
      const todayStr = new Date().toISOString().slice(0, 10);

      // Midnight reset: if date changed, reset accumulators
      if (this._accumDate && this._accumDate !== todayStr) {
        this._energyDayWs = 0;
        this._energyNightWs = 0;
        this._accumDate = todayStr;
        this._savePanelSettings({ _accum_day_ws: 0, _accum_night_ws: 0, _accum_date: todayStr });
      }

      if (this._lastAccumTs && load > 0) {
        let dtSec = (nowTs - this._lastAccumTs) / 1000;
        if (dtSec > 0 && dtSec < 60) { // cap at 60s to avoid spikes after sleep
          const wattSec = load * dtSec;
          if (isDay) this._energyDayWs += wattSec;
          else this._energyNightWs += wattSec;
        }
      }
      this._lastAccumTs = nowTs;

      // Persist accumulators every 5 minutes
      if (nowTs - this._lastAccumSaveTs > 300000) {
        this._lastAccumSaveTs = nowTs;
        this._savePanelSettings({
          _accum_day_ws: Math.round(this._energyDayWs),
          _accum_night_ws: Math.round(this._energyNightWs),
          _accum_date: todayStr
        });
      }

      // Compute calibrated day/night kWh from load_today
      const loadTodayNum = typeof loadTodayVal === 'number' ? loadTodayVal : parseFloat(loadTodayVal);
      const totalAccumWs = this._energyDayWs + this._energyNightWs;
      let dayKwh = 0, nightKwh = 0;
      if (totalAccumWs > 0 && !isNaN(loadTodayNum) && loadTodayNum > 0) {
        const dayRatio = this._energyDayWs / totalAccumWs;
        dayKwh = loadTodayNum * dayRatio;
        nightKwh = loadTodayNum * (1 - dayRatio);
      } else if (totalAccumWs > 0) {
        dayKwh = this._energyDayWs / 3600000;
        nightKwh = this._energyNightWs / 3600000;
      }

      // PV to home — capped at load_today (can't consume more PV than total load)
      const pvTodayForHome = this._nm("pv_today") || parseFloat(this._fm("pv_today")) || 0;
      const expTodayForHome = this._nm("grid_export_today") || 0;
      const pvToHomeRaw = Math.max(0, pvTodayForHome - expTodayForHome);
      const loadTodayCap = !isNaN(loadTodayNum) && loadTodayNum > 0 ? loadTodayNum : Infinity;
      const pvToHome = Math.min(pvToHomeRaw, loadTodayCap);

      // PV-based fallback: if accumulators missed daytime (e.g. restart after sunset)
      // PV self-consumption is the physical minimum for day consumption
      if (dayKwh === 0 && pvToHome > 0 && !isNaN(loadTodayNum) && loadTodayNum > 0) {
        dayKwh = Math.min(loadTodayNum, pvToHome);
        nightKwh = Math.max(0, loadTodayNum - dayKwh);
      }

      const loadTotalForPct = !isNaN(loadTodayNum) && loadTodayNum > 0 ? loadTodayNum : (dayKwh + nightKwh);
      const pvToHomePct = loadTotalForPct > 0 ? Math.min(100, (pvToHome / loadTotalForPct) * 100) : 0;

      // Update Overview 🏠 Zużycie breakdown
      this._setText("v-load-day", dayKwh > 0 ? `${dayKwh.toFixed(1)}` : "—");
      this._setText("v-load-night", nightKwh > 0 ? `${nightKwh.toFixed(1)}` : "—");
      this._setText("v-load-from-pv", pvToHome > 0 ? `${pvToHome.toFixed(1)} kWh` : "— kWh");
      this._setText("v-load-from-pv-pct", pvToHome > 0 ? `(${pvToHomePct.toFixed(0)}%)` : "");
    }

    // Dynamic Home node coloring: green if self-sufficient, orange if grid needed
    const homeNode = this.shadowRoot.getElementById("home-node");
    const homeBig = this.shadowRoot.getElementById("v-load");
    if (homeNode) {
      const selfSufficient = grid <= 10; // not importing from grid
      const hColor = selfSufficient ? "rgba(46,204,113," : "rgba(243,156,18,";
      homeNode.style.borderColor = hColor + "0.5)";
      homeNode.style.boxShadow = `0 8px 32px 0 ${hColor}0.2)`;
      if (homeBig) homeBig.style.color = selfSufficient ? "#2ecc71" : "#f39c12";
    }

    // Grid
    this._setText("v-grid", this._pw(Math.abs(grid)));
    this._setText("v-grid-dir", grid > 0 ? "POBÓR Z SIECI" : grid < -10 ? "ODDAWANIE DO SIECI" : "");
    this._setText("v-grid-import", `${this._fm("grid_import_today")} kWh`);
    this._setText("v-grid-export", `${this._fm("grid_export_today")} kWh`);
    this._setText("v-grid-v1", `${this._fm("voltage_l1")} V`);
    this._setText("v-grid-v2", `${this._fm("voltage_l2")} V`);
    this._setText("v-grid-v3", `${this._fm("voltage_l3")} V`);
    this._setText("v-grid-freq", `${this._fm("grid_frequency", 2)} Hz`);
    // Dynamic grid node coloring
    const gridNode = this.shadowRoot.getElementById("grid-node");
    const gridBig = this.shadowRoot.getElementById("v-grid");
    const gridDirEl = this.shadowRoot.getElementById("v-grid-dir");
    if (gridNode) {
      if (grid > 10) {
        gridNode.style.borderColor = "rgba(231,76,60,0.5)";
        gridNode.style.boxShadow = "0 8px 32px 0 rgba(231,76,60,0.2)";
        if (gridDirEl) gridDirEl.style.color = "#e74c3c";
        if (gridBig) gridBig.style.color = "#e74c3c";
      } else if (grid < -10) {
        gridNode.style.borderColor = "rgba(46,204,113,0.5)";
        gridNode.style.boxShadow = "0 8px 32px 0 rgba(46,204,113,0.2)";
        if (gridDirEl) gridDirEl.style.color = "#2ecc71";
        if (gridBig) gridBig.style.color = "#2ecc71";
      } else {
        gridNode.style.borderColor = "rgba(255,255,255,0.1)";
        gridNode.style.boxShadow = "0 8px 32px 0 rgba(0,0,0,0.3)";
        if (gridDirEl) gridDirEl.style.color = "#64748b";
        if (gridBig) gridBig.style.color = "#fff";
      }
    }

    // Battery (GoodWe BT: positive = discharging, negative = charging)
    this._setText("v-batt", this._pw(Math.abs(batt)));
    this._setText("v-batt-dir", batt < -10 ? "ŁADOWANIE ↑" : batt > 10 ? "ROZŁADOWANIE ↓" : "STANDBY");
    this._setText("v-soc", `${Math.round(soc)}%`);
    this._setText("v-batt-v", `${this._fm("battery_voltage")} V`);
    this._setText("v-batt-a", `${this._fm("battery_current")} A`);
    this._setText("v-batt-temp", `${this._fm("battery_temp")}°C`);
    this._setText("v-batt-charge", `↑ ${this._fm("battery_charge_today")} kWh`);
    this._setText("v-batt-discharge", `↓ ${this._fm("battery_discharge_today")} kWh`);

    // Battery ETA — czas do pełna / rozładowania
    const battEtaEl = this.shadowRoot.getElementById("v-batt-eta");
    const battEtaText = this.shadowRoot.getElementById("v-batt-eta-text");
    if (battEtaEl && battEtaText) {
      const battCap = this._settings.battery_capacity_kwh || 10.2;
      const absPower = Math.abs(batt);
      if (absPower > 10) {
        const powerKw = absPower / 1000;
        let remainKwh, label, color;
        if (batt < -10) {
          // Ładowanie → czas do 100%
          remainKwh = ((100 - soc) / 100) * battCap;
          label = "do pełna";
          color = "#2ecc71";
        } else {
          // Rozładowanie → czas do 0%
          remainKwh = (soc / 100) * battCap;
          label = "do rozładowania";
          color = "#f39c12";
        }
        const etaH = remainKwh / powerKw;
        let timeStr;
        if (etaH < 1/60) timeStr = "< 1 min";
        else if (etaH > 24) timeStr = "> 24h";
        else if (etaH < 1) timeStr = `${Math.round(etaH * 60)}min`;
        else timeStr = `${Math.floor(etaH)}h ${Math.round((etaH - Math.floor(etaH)) * 60)}min`;
        battEtaText.textContent = `~${timeStr} ${label}`;
        battEtaEl.style.color = (batt > 10 && etaH < 0.5) ? "#e74c3c" : color;
        battEtaEl.style.animation = (batt > 10 && etaH < 0.5) ? "etaPulseFast 1.2s ease-in-out infinite" : "etaPulse 2.5s ease-in-out infinite";
        battEtaEl.style.display = "";
      } else {
        battEtaEl.style.display = "none";
      }
    }
    // Dynamic Battery node coloring: green=charging, orange=discharging
    const battNode = this.shadowRoot.getElementById("batt-node");
    const battDirEl = this.shadowRoot.getElementById("v-batt-dir");
    if (battNode) {
      if (batt < -10) {
        // Charging (negative = charging for GoodWe BT)
        battNode.style.borderColor = "rgba(46,204,113,0.5)";
        battNode.style.boxShadow = "0 8px 32px 0 rgba(46,204,113,0.2)";
        if (battDirEl) battDirEl.style.color = "#2ecc71";
      } else if (batt > 10) {
        // Discharging (positive = discharging for GoodWe BT)
        battNode.style.borderColor = "rgba(243,156,18,0.5)";
        battNode.style.boxShadow = "0 8px 32px 0 rgba(243,156,18,0.2)";
        if (battDirEl) battDirEl.style.color = "#f39c12";
      } else {
        // Standby
        battNode.style.borderColor = "rgba(0,212,255,0.2)";
        battNode.style.boxShadow = "0 8px 32px 0 rgba(0,0,0,0.3)";
        if (battDirEl) battDirEl.style.color = "#64748b";
      }
    }

    // Battery Graphic Bars (10 levels)
    const battGraphic = this.shadowRoot.getElementById("batt-graphic");
    if (battGraphic) {
      const barsCount = 10;
      const activeBars = Math.round((soc / 100) * barsCount);
      let color = '#2ecc71';
      if (soc <= 20) color = '#e74c3c';
      else if (soc <= 50) color = '#f39c12';

      const battBars = battGraphic.querySelectorAll('.batt-bar');
      battBars.forEach((bar, index) => {
        // column-reverse: index 0 = bottom (level 1), index 9 = top (level 10)
        const barLevel = index + 1;
        bar.style.animation = 'none';
        bar.style.opacity = '1';
        bar.style.boxShadow = 'none';

        if (barLevel <= activeBars) {
          bar.style.backgroundColor = color;
          bar.style.color = color;
          
          if (batt < -10) {
            // Charging — pulse upwards (bottom bars first)
            bar.style.animation = `batt-pulse-up 1.5s infinite ${(barLevel - 1) * 0.12}s`;
          } else if (batt > 10) {
            // Discharging — pulse downwards (top bars first)
            bar.style.animation = `batt-pulse-down 1.5s infinite ${(barsCount - barLevel) * 0.12}s`;
          } else {
            // Standby — static glow
            bar.style.boxShadow = `0 0 6px ${color}60`;
          }
        } else {
          bar.style.backgroundColor = 'rgba(255,255,255,0.06)';
          bar.style.color = 'transparent';
        }
      });
    }
    const socEl = this.shadowRoot.getElementById("v-soc");
    if (socEl) socEl.style.color = soc > 50 ? "#2ecc71" : soc > 20 ? "#f39c12" : "#e74c3c";

    // Inverter
    this._setText("v-inv-p", this._pw(Math.abs(this._nm("inverter_power") || 0)));
    this._setText("v-inv-t", `${this._fm("inverter_temp")}°C`);

    // Autarky / Self-consumption — compute from daily totals
    {
      const impToday = this._nm("grid_import_today") || 0;
      const pvToday = parseFloat(this._fm("pv_today")) || 0;
      if (pvToday > 0 || impToday > 0) {
        const totalConsumed = pvToday + impToday;
        const autarky = totalConsumed > 0 ? Math.min(100, Math.max(0, (pvToday / totalConsumed) * 100)) : 0;
        this._setText("v-autarky", `${autarky.toFixed(0)}%`);
      } else {
        const pvNow = this._nm("pv_power") || 0;
        const loadNow = this._nm("load_power") || 0;
        if (loadNow > 0) {
          const rtAutarky = Math.min(100, Math.max(0, (pvNow / loadNow) * 100));
          this._setText("v-autarky", `${rtAutarky.toFixed(0)}%`);
        } else {
          this._setText("v-autarky", "0%");
        }
      }
    }
    {
      const expToday = this._nm("grid_export_today") || 0;
      const pvGen = parseFloat(this._fm("pv_today")) || 0;
      if (pvGen > 0) {
        const selfCons = Math.min(100, Math.max(0, ((pvGen - expToday) / pvGen) * 100));
        this._setText("v-selfcons", `${selfCons.toFixed(0)}%`);
      } else {
        const pvNow = this._nm("pv_power") || 0;
        this._setText("v-selfcons", pvNow > 0 ? "100%" : "0%");
      }
    }

    // Weather — prefer Ecowitt if enabled, then auto-discover weather entity
    if (this._settings.ecowitt_enabled && this._hass?.states?.['sensor.ecowitt_outdoor_temp_9747']) {
      const ecoTemp = this._n('sensor.ecowitt_outdoor_temp_9747');
      const ecoHumid = this._n('sensor.ecowitt_outdoor_humidity_9747');
      this._setText("v-weather", ecoTemp != null ? `${Math.round(ecoTemp)}°C` : "");
      this._setText("v-clouds", ecoHumid != null ? `${ecoHumid}%` : "—%");
      // Update v-clouds label to 'Wilgotność' since Ecowitt doesn't have cloud%
      const cloudLabel = this.shadowRoot.querySelector('#v-clouds')?.closest('.summary-item')?.querySelector('.si-label');
      if (cloudLabel) cloudLabel.textContent = '💧 Wilgotność';
    } else {
      const weatherPriority = ["weather.dom", "weather.home", "weather.accuweather", "weather.forecast_home", "weather.openweathermap"];
      let weatherState = null;
      for (const we of weatherPriority) {
        if (this._hass?.states[we]) { weatherState = this._hass.states[we]; break; }
      }
      if (!weatherState && this._hass?.states) {
        const wk = Object.keys(this._hass.states).find(k => k.startsWith('weather.'));
        if (wk) weatherState = this._hass.states[wk];
      }
      if (weatherState) {
        const wTemp = weatherState.attributes?.temperature;
        const wHumid = weatherState.attributes?.humidity;
        const wCloud = weatherState.attributes?.cloud_coverage;
        this._setText("v-weather", wTemp != null ? `${Math.round(wTemp)}°C` : "");
        this._setText("v-clouds", wCloud != null ? `${Math.round(wCloud)}%` : (wHumid != null ? `${Math.round(wHumid)}%` : "—%"));
      } else {
        const wt = this._nm("weather_temp");
        this._setText("v-weather", wt !== null ? `${wt.toFixed(0)}°C` : "");
        this._setText("v-clouds", this._fm("weather_cloud_cover", 0) + "%");
      }
    }

    // Animated flows
    const pvSurplus = pv > load && pv > 10;
    const battCharging = batt < -10;

    this._flow("fl-pv-inv", pv > 10 && !pvSurplus, pv);
    this._flow("fl-pv-inv-bolt", pv > 10 && pvSurplus, pv);
    
    this._flow("fl-inv-load", load > 10, load);
    this._flow("fl-grid-inv", grid > 10, grid);
    this._flow("fl-inv-grid", grid < -10, Math.abs(grid));
    
    this._flow("fl-inv-batt", battCharging && !pvSurplus, Math.abs(batt));
    this._flow("fl-inv-batt-pv", battCharging && pvSurplus, Math.abs(batt));
    this._flow("fl-batt-inv", batt > 10, batt);

    // Dynamic Inverter Image Logic
    let imgName = "goodwe"; // domyślnie GoodWe
    const invModel = (this._s(this._m("inverter_model")) || "").toLowerCase();
    const userChoice = (this._s("input_select.smartinghome_inverter_model") || "").toLowerCase();

    if (invModel.includes("deye") || userChoice.includes("deye")) {
      imgName = "deye";
    } else if (invModel.includes("growatt") || userChoice.includes("growatt")) {
      imgName = "growatt";
    } else if (invModel.includes("goodwe") || userChoice.includes("goodwe")) {
      imgName = "goodwe";
    }

    const imgEl = this.shadowRoot.getElementById("v-inv-img");
    if (imgEl && imgEl.getAttribute("data-model") !== imgName) {
      imgEl.setAttribute("data-model", imgName);
      const iconEl = this.shadowRoot.getElementById("v-inv-icon");
      const remoteFallbackMap = {
        deye: 'https://smartinghome.pl/wp-content/uploads/2026/03/Deye-1.png',
        growatt: 'https://smartinghome.pl/wp-content/uploads/2026/03/Growatt.png',
        goodwe: 'https://smartinghome.pl/wp-content/uploads/2026/03/GoodWe-1.png',
      };
      const remoteFallback = remoteFallbackMap[imgName] || remoteFallbackMap.goodwe;
      // Only try local paths if user has uploaded a custom image
      if (this._settings.custom_inverter_image) {
        const localBrand = `/local/smartinghome/${imgName}.png`;
        const localGeneric = `/local/smartinghome/inverter.png`;
        (async () => {
          for (const url of [localBrand, localGeneric]) {
            try { const r = await fetch(url, {method:'HEAD'}); if (r.ok) { imgEl.src = url + '?t=' + Date.now(); imgEl.style.display = 'block'; if (iconEl) iconEl.style.display = 'none'; return; } } catch(e) {}
          }
          imgEl.src = remoteFallback;
          imgEl.style.display = 'block';
          if (iconEl) iconEl.style.display = 'none';
        })();
      } else {
        imgEl.src = remoteFallback;
        imgEl.style.display = 'block';
        if (iconEl) iconEl.style.display = 'none';
      }
    }
  }

  _updateSvgPaths() {
    const wrap = this.shadowRoot.querySelector('.flow-wrapper');
    const svg = this.shadowRoot.querySelector('.flow-svg-bg');
    if (!wrap || !svg) return;
    const wr = wrap.getBoundingClientRect();
    if (wr.width === 0 || wr.height === 0) return;
    
    svg.setAttribute('viewBox', `0 0 ${wr.width} ${wr.height}`);
    svg.removeAttribute('preserveAspectRatio');
    
    const getNode = (id) => {
      const el = this.shadowRoot.getElementById(id);
      if(!el) return null;
      const r = el.getBoundingClientRect();
      const off = r.height * 0.20;
      return {
        x: r.left - wr.left + r.width/2,
        top: r.top - wr.top + off,
        bot: r.top - wr.top + r.height - off,
        cy: r.top - wr.top + r.height/2
      };
    };
    
    const pv = getNode('pv-node');
    const load = getNode('home-node');
    const batt = getNode('batt-node');
    const grid = getNode('grid-node');
    
    const invEl = this.shadowRoot.querySelector('.inv-box');
    let inv = {x: wr.width/2, top: wr.height*0.35, bot: wr.height*0.55};
    if (invEl) {
      const ir = invEl.getBoundingClientRect();
      inv = { x: ir.left - wr.left + ir.width/2, top: ir.top - wr.top, bot: ir.top - wr.top + ir.height };
    }
    
    if(!pv || !load || !batt || !grid) return;

    const mkPath = (x1, y1, x2, y2, mode) => {
      if (mode === 'H') return `M ${x1},${y1} H ${x2} V ${y2}`;
      if (mode === 'V') return `M ${x1},${y1} V ${y2} H ${x2}`;
      if (mode === 'B') {
        const busY = wr.height * 0.85;
        return `M ${x1},${y1} V ${busY} H ${x2} V ${y2}`;
      }
      return `M ${x1},${y1} L ${x2},${y2}`;
    };

    const updateLine = (lineId, anims, x1, y1, x2, y2, mode) => {
      const p = mkPath(x1, y1, x2, y2, mode);
      const lp = this.shadowRoot.getElementById(lineId);
      if(lp) lp.setAttribute('d', p);
      for(let animId of anims) {
         const am = this.shadowRoot.getElementById(animId);
         if(am) am.setAttribute('path', p);
      }
    };
    
    updateLine('line-pv-inv', ['anim-pv-inv', 'anim-pv-inv-bolt'], pv.x, pv.top, inv.x, inv.top, 'H');
    updateLine('line-inv-load', ['anim-inv-load'], inv.x, inv.top, load.x, load.top, 'V');
    updateLine('line-batt-inv', [], batt.x, batt.bot, inv.x, inv.bot, 'B'); 
    
    const rpBatt = mkPath(batt.x, batt.bot, inv.x, inv.bot, 'B'); 
    const amBattInv = this.shadowRoot.getElementById('anim-batt-inv');
    if(amBattInv) amBattInv.setAttribute('path', rpBatt);
    
    const pInvBatt = mkPath(inv.x, inv.bot, batt.x, batt.bot, 'B'); 
    const amInvBatt = this.shadowRoot.getElementById('anim-inv-batt');
    if(amInvBatt) amInvBatt.setAttribute('path', pInvBatt);
    const amInvBattPv = this.shadowRoot.getElementById('anim-inv-batt-pv');
    if(amInvBattPv) amInvBattPv.setAttribute('path', pInvBatt);

    updateLine('line-grid-inv', [], grid.x, grid.bot, inv.x, inv.bot, 'B'); 
    const rpGrid = mkPath(grid.x, grid.bot, inv.x, inv.bot, 'B'); 
    const amGridInv = this.shadowRoot.getElementById('anim-grid-inv');
    if(amGridInv) amGridInv.setAttribute('path', rpGrid);
    
    const pInvGrid = mkPath(inv.x, inv.bot, grid.x, grid.bot, 'B'); 
    const amInvGrid = this.shadowRoot.getElementById('anim-inv-grid');
    if(amInvGrid) amInvGrid.setAttribute('path', pInvGrid);
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

  _updateHomeImage() {
    const imgEl = this.shadowRoot.getElementById("v-home-img");
    if (!imgEl || imgEl.getAttribute("data-loaded") === "1") return;
    imgEl.setAttribute("data-loaded", "1");
    const remoteFallback = 'https://smartinghome.pl/wp-content/uploads/2026/03/grafika-domu.png';
    // Only try local path if user has uploaded a custom home image
    if (this._settings.custom_home_image) {
      const localPath = '/local/smartinghome/home.png';
      (async () => {
        try {
          const r = await fetch(localPath, {method:'HEAD'});
          if (r.ok) { imgEl.src = localPath + '?t=' + Date.now(); return; }
        } catch(e) {}
        imgEl.src = remoteFallback;
      })();
    } else {
      imgEl.src = remoteFallback;
    }
  }

  _updateStats() {
    // HEMS recommendation — only overwrite if no AI content loaded
    if (!this._aiHemsLoaded) {
      this._setText("v-hems-rec", this._s("sensor.smartinghome_hems_recommendation") || "Brak danych");
    }
    // License
    const badge = this.shadowRoot.getElementById("v-license");
    const tier = this._tier();
    if (badge) {
      badge.textContent = tier;
      badge.className = `badge ${tier === "PRO" || tier === "ENTERPRISE" ? "pro" : "free"}`;
    }
    // Settings: toggle upgrade prompt vs PRO box
    const upgradeBox = this.shadowRoot.getElementById("settings-upgrade-box");
    const proBox = this.shadowRoot.getElementById("settings-pro-box");
    const isPro = (tier === "PRO" || tier === "ENTERPRISE");
    if (upgradeBox) upgradeBox.style.display = isPro ? "none" : "block";
    if (proBox) proBox.style.display = isPro ? "block" : "none";
    // Settings: update PRO tier label
    const tierLabel = this.shadowRoot.getElementById("settings-license-tier");
    if (tierLabel) tierLabel.textContent = tier;
    // Settings: API key status
    this._updateKeyStatus();
    // ROI Tab
    this._updateRoi();
    // Overview daily financial KPIs
    // GoodWe swap: g13_import_cost actually has export revenue, and vice versa
    const ovCost = this._n("sensor.g13_export_revenue_today") ?? this._n("sensor.g13_export_revenue_daily") ?? 0;
    const ovRev = this._n("sensor.g13_import_cost_today") ?? this._n("sensor.g13_import_cost_daily") ?? 0;
    let ovSav = this._n("sensor.g13_self_consumption_savings_today") ?? this._n("sensor.g13_self_consumption_savings_daily") ?? 0;
    // Fallback: compute savings from self-consumed PV × G13 avg price when sensor is 0
    if (ovSav === 0) {
      const pvToday = this._n("sensor.today_s_pv_generation") ?? 0;
      // GoodWe swap: grid_import_daily = our export
      const expKWh = this._n("sensor.grid_import_daily") ?? 0;
      const selfConsumed = Math.max(pvToday - expKWh, 0);
      if (selfConsumed > 0) ovSav = selfConsumed * this._getTariffAvgPrice();
    }
    // Always compute balance from corrected components (backend sensor has swapped sign)
    const ovBal = ovRev + ovSav - ovCost;
    this._setText("ov-cost", `${ovCost.toFixed(2)} zł`);
    this._setText("ov-revenue", `${ovRev.toFixed(2)} zł`);
    this._setText("ov-savings", `${ovSav.toFixed(2)} zł`);
    this._setText("ov-balance", `${ovBal >= 0 ? "+" : ""}${ovBal.toFixed(2)} zł`);
    const ovBalCard = this.shadowRoot.getElementById("ov-balance-card");
    const ovBalText = this.shadowRoot.getElementById("ov-balance");
    if (ovBalCard) { ovBalCard.style.borderColor = ovBal >= 0 ? "#2ecc71" : "#e74c3c"; ovBalCard.style.background = ovBal >= 0 ? "rgba(46,204,113,0.08)" : "rgba(231,76,60,0.08)"; }
    if (ovBalText) ovBalText.style.color = ovBal >= 0 ? "#2ecc71" : "#e74c3c";
    // RCE / Tariff — G13 Zone Badge
    const g13Zone = this._s("sensor.smartinghome_g13_current_zone") || this._s("sensor.g13_current_zone") || "—";
    const g13Badge = this.shadowRoot.getElementById("v-g13-zone-badge");
    if (g13Badge) {
      g13Badge.textContent = g13Zone.toUpperCase();
      const isPeak = g13Zone.toLowerCase().includes("szczytow") && !g13Zone.toLowerCase().includes("poza");
      g13Badge.className = `status-badge ${isPeak ? 'peak' : 'offpeak'}`;
    }
    
    // G13 Price
    const g13Price = this._n("sensor.smartinghome_g13_buy_price") ?? this._n("sensor.g13_buy_price");
    this._setText("v-g13-price-tab", g13Price !== null ? `${g13Price.toFixed(2)} zł/kWh` : "— zł/kWh");
    
    // RCE Sell price
    const rceSell = this._n("sensor.smartinghome_rce_sell_price") ?? this._n("sensor.rce_sell_price");
    this._setText("v-rce-sell", rceSell !== null ? `${rceSell.toFixed(4)} zł/kWh` : "— zł/kWh");
    
    // Spread G13↔RCE
    if (g13Price !== null && rceSell !== null) {
      const spread = g13Price - rceSell;
      const spreadEl = this.shadowRoot.getElementById("v-spread");
      if (spreadEl) {
        spreadEl.textContent = `${spread.toFixed(2)} zł`;
        spreadEl.style.color = spread > 0 ? "#e74c3c" : "#2ecc71";
      }
    }

    // RCE Now (big card, zł/kWh)
    const rceNowKwh = this._n("sensor.rce_pse_cena_za_kwh") ?? rceSell;
    const rceNowMwh = this._n("sensor.rce_pse_cena");
    const rceNowEl = this.shadowRoot.getElementById("v-rce-now");
    if (rceNowEl && rceNowKwh !== null) {
      rceNowEl.textContent = `${rceNowKwh.toFixed(2)} zł`;
      rceNowEl.style.color = rceNowKwh > 0.6 ? "#2ecc71" : rceNowKwh > 0.3 ? "#f7b731" : "#e74c3c";
    }
    this._setText("v-rce-now-mwh", rceNowMwh !== null ? `${rceNowMwh.toFixed(0)} PLN/MWh` : "— PLN/MWh");

    // RCE +1h, +2h, +3h
    const rce1h = this._n("sensor.rce_sell_price_next_hour") ?? this._n("sensor.smartinghome_rce_sell_price_next_hour");
    const rce2h = this._n("sensor.rce_sell_price_2h") ?? this._n("sensor.smartinghome_rce_sell_price_2h");
    const rce3h = this._n("sensor.rce_sell_price_3h") ?? this._n("sensor.smartinghome_rce_sell_price_3h");
    this._setText("v-rce-1h", rce1h !== null ? rce1h.toFixed(2) : "—");
    this._setText("v-rce-2h", rce2h !== null ? rce2h.toFixed(2) : "—");
    this._setText("v-rce-3h", rce3h !== null ? rce3h.toFixed(2) : "—");

    // RCE Statistics
    const rceAvg = this._n("sensor.rce_average_today") ?? this._n("sensor.smartinghome_rce_average_today");
    const rceMin = this._n("sensor.rce_min_today") ?? this._n("sensor.smartinghome_rce_min_today");
    const rceMax = this._n("sensor.rce_max_today") ?? this._n("sensor.smartinghome_rce_max_today");
    this._setText("v-rce-avg2", rceAvg !== null ? rceAvg.toFixed(2) : "—");
    this._setText("v-rce-min", rceMin !== null ? rceMin.toFixed(2) : "—");
    this._setText("v-rce-max", rceMax !== null ? rceMax.toFixed(2) : "—");

    // vs Średnia
    const rceVsAvg = this._n("sensor.rce_pse_aktualna_vs_srednia_dzisiaj");
    const vsAvgEl = this.shadowRoot.getElementById("v-rce-vs-avg");
    if (vsAvgEl && rceVsAvg !== null) {
      vsAvgEl.textContent = `${rceVsAvg > 0 ? '+' : ''}${rceVsAvg.toFixed(1)}%`;
      vsAvgEl.style.color = rceVsAvg > 10 ? "#2ecc71" : rceVsAvg < -10 ? "#e74c3c" : "#f7b731";
      this._setText("v-rce-vs-label", rceVsAvg > 0 ? "powyżej średniej" : "poniżej średniej");
    }

    // Trend
    const rceTrend = this._s("sensor.smartinghome_rce_price_trend") || this._s("sensor.rce_price_trend") || "—";
    const trendEl = this.shadowRoot.getElementById("v-rce-trend2");
    if (trendEl) {
      if (rceTrend === "rosnie") { trendEl.textContent = "📈"; this._setText("v-rce-trend-label", "Rośnie — warto czekać"); }
      else if (rceTrend === "spada") { trendEl.textContent = "📉"; this._setText("v-rce-trend-label", "Spada"); }
      else { trendEl.textContent = "➖"; this._setText("v-rce-trend-label", "Stabilny"); }
    }

    // Time Windows
    this._setText("v-cheapest-window", this._s("sensor.rce_pse_najtansze_okno_czasowe_dzisiaj") || "—");
    this._setText("v-expensive-window", this._s("sensor.rce_pse_najdrozsze_okno_czasowe_dzisiaj") || "—");
    this._setText("v-kompas", this._s("sensor.rce_pse_kompas_energetyczny_dzisiaj") || "—");

    // RCE Grade
    const rceGrade = this._s("sensor.rce_good_sell") || this._s("sensor.smartinghome_rce_good_sell") || "—";
    const gradeEl = this.shadowRoot.getElementById("v-rce-grade");
    if (gradeEl) {
      const gradeMap = { excellent: { t: "🟢 EXCELLENT", c: "#2ecc71" }, good: { t: "🟡 GOOD", c: "#f7b731" }, poor: { t: "🟠 POOR", c: "#e67e22" }, terrible: { t: "🔴 TERRIBLE", c: "#e74c3c" } };
      const g = gradeMap[rceGrade.toLowerCase()] || { t: rceGrade.toUpperCase(), c: "#94a3b8" };
      gradeEl.textContent = g.t; gradeEl.style.color = g.c;
    }

    // RCE median + tomorrow stats
    const rceMedianMwh = this._n("sensor.rce_pse_mediana_cen_dzisiaj");
    this._setText("v-rce-median", rceMedianMwh !== null ? `${(rceMedianMwh / 1000 * 1.23).toFixed(4)} zł` : "— zł");
    const rceAvgTomorrowMwh = this._n("sensor.rce_pse_srednia_cena_jutro");
    this._setText("v-rce-avg-tomorrow", rceAvgTomorrowMwh !== null ? `${(rceAvgTomorrowMwh / 1000 * 1.23).toFixed(4)} zł` : "— zł");
    const rceTomorrowVs = this._n("sensor.rce_pse_jutro_vs_dzisiaj_srednia");
    this._setText("v-rce-tomorrow-vs", rceTomorrowVs !== null ? `${rceTomorrowVs > 0 ? '+' : ''}${rceTomorrowVs.toFixed(1)}%` : "—%");
    this._setText("v-cheapest-tomorrow", this._s("sensor.rce_pse_najtansze_okno_czasowe_jutro") || "—");
    this._setText("v-expensive-tomorrow", this._s("sensor.rce_pse_najdrozsze_okno_czasowe_jutro") || "—");

    // HEMS Recommendation (tariff tab) — skip if AI content already loaded
    if (!this._aiTariffLoaded) {
      this._setText("v-hems-rec-tariff", this._s("sensor.hems_rce_recommendation") || this._s("sensor.smartinghome_hems_recommendation") || "—");
    }

    // Economics
    // GoodWe swap: g13_import_cost has export revenue, and vice versa
    const savings = this._n("sensor.g13_self_consumption_savings_today") ?? this._n("sensor.smartinghome_self_consumption_savings_today");
    const expRev = this._n("sensor.g13_import_cost_today") ?? this._n("sensor.smartinghome_export_revenue_today");
    const impCost = this._n("sensor.g13_export_revenue_today") ?? this._n("sensor.smartinghome_import_cost_today");
    // Compute balance from corrected components (backend sensor has swapped sign)
    const netBal = (expRev ?? 0) + (savings ?? 0) - (impCost ?? 0);
    this._setText("v-savings", savings !== null ? savings.toFixed(2) : "—");
    this._setText("v-export-rev", expRev !== null ? expRev.toFixed(2) : "—");
    this._setText("v-import-cost", impCost !== null ? impCost.toFixed(2) : "—");
    const netEl = this.shadowRoot.getElementById("v-net-balance");
    if (netEl) { netEl.textContent = netBal.toFixed(2); netEl.style.color = netBal >= 0 ? "#2ecc71" : "#e74c3c"; }
    
    const ftoday = this._n("sensor.smartinghome_pv_forecast_today_total")
      ?? this._n("sensor.energy_production_today")
      ?? this._n("sensor.solcast_pv_forecast_today")
      ?? this._n("sensor.forecast_solar_energy_production_today");
    const ftomor = this._n("sensor.smartinghome_pv_forecast_tomorrow_total")
      ?? this._n("sensor.energy_production_tomorrow")
      ?? this._n("sensor.solcast_pv_forecast_tomorrow")
      ?? this._n("sensor.forecast_solar_energy_production_tomorrow");
    this._setText("v-forecast-today", ftoday !== null ? `${ftoday.toFixed(1)} kWh` : "— kWh");
    this._setText("v-forecast-tomorrow", ftomor !== null ? `${ftomor.toFixed(1)} kWh` : "— kWh");
    this._setText("v-forecast-today-tab", ftoday !== null ? `${ftoday.toFixed(1)} kWh` : "— kWh");
    this._setText("v-forecast-tomorrow-tab", ftomor !== null ? `${ftomor.toFixed(1)} kWh` : "— kWh");
    
    // AccuWeather data for Energy tab
    const wEntity = this._hass?.states?.["weather.dom"] || this._hass?.states?.["weather.forecast_dom"];
    if (wEntity) {
      this._setText("v-energy-temp", `${wEntity.attributes?.temperature ?? '—'}°C`);
      this._setText("v-energy-clouds", `${wEntity.attributes?.cloud_coverage ?? '—'}%`);
      this._setText("v-energy-condition", wEntity.state || '—');
      const windSpeed = this._s("sensor.dom_predkosc_wiatru_dzien_0")
        || wEntity.attributes?.wind_speed;
      if (windSpeed) {
        const wsNum = parseFloat(windSpeed);
        const msVal = !isNaN(wsNum) ? ` (${(wsNum / 3.6).toFixed(1)} m/s)` : '';
        this._setText("v-energy-wind", `${windSpeed} km/h${msVal}`);
      } else {
        this._setText("v-energy-wind", '—');
      }
    } else {
      const ws = this._s("sensor.dom_predkosc_wiatru_dzien_0");
      if (ws) {
        const wsNum = parseFloat(ws);
        const msVal = !isNaN(wsNum) ? ` (${(wsNum / 3.6).toFixed(1)} m/s)` : '';
        this._setText("v-energy-wind", `${ws} km/h${msVal}`);
      } else {
        this._setText("v-energy-wind", '—');
      }
    }
    this._setText("v-energy-realfeel", `${this._s("sensor.dom_temperatura_realfeel") || '—'}°C`);
    this._setText("v-energy-sunhours", `${this._s("sensor.dom_godziny_sloneczne_dzien_0") || '—'} h`);
    this._setText("v-energy-uv", this._s("sensor.dom_indeks_uv_dzien_0") || '—');
    
    // Battery energy: SOC × capacity
    const battCapOv = this._settings.battery_capacity_kwh || 10.2;
    const socOv = this._nm("battery_soc") || 0;
    const battEnergyOv = socOv > 0 ? (socOv / 100) * battCapOv : 0;
    this._setText("v-battery-energy-tab", `${battEnergyOv.toFixed(1)} kWh`);
    
    // Battery runtime: energy / load
    const loadOv = this._nm("load_power") || 0;
    const loadKwOv = loadOv > 0 ? loadOv / 1000 : 0;
    if (battEnergyOv > 0 && loadKwOv > 0) {
      const rtH = battEnergyOv / loadKwOv;
      this._setText("v-battery-runtime-tab", `${Math.floor(rtH)}h ${Math.round((rtH - Math.floor(rtH)) * 60)}min`);
    } else {
      this._setText("v-battery-runtime-tab", loadKwOv === 0 ? "∞" : "—");
    }
    
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

    // Battery Tab: Live parameters
    this._setText("v-batt-v-tab", `${this._fm("battery_voltage")} V`);
    this._setText("v-batt-a-tab", `${this._fm("battery_current")} A`);
    this._setText("v-batt-temp-tab", `${this._fm("battery_temp")}°C`);
    const battPower = this._nm("battery_power") || 0;
    this._setText("v-batt-p-tab", this._pw(Math.abs(battPower)));

    // Dynamic status box
    const statusBox = this.shadowRoot.getElementById("batt-status-box");
    const statusDot = this.shadowRoot.getElementById("batt-status-dot");
    const statusText = this.shadowRoot.getElementById("batt-status-text");
    const statusPower = this.shadowRoot.getElementById("batt-status-power");
    if (statusBox && statusDot && statusText && statusPower) {
      statusPower.textContent = this._pw(Math.abs(battPower));
      if (battPower > 10) {
        // Discharging (GoodWe BT: positive = discharge)
        statusBox.style.background = "rgba(243,156,18,0.1)";
        statusDot.style.background = "#f39c12";
        statusText.textContent = "ROZŁADOWANIE ↓";
        statusText.style.color = "#f39c12";
        statusPower.style.color = "#f39c12";
      } else if (battPower < -10) {
        // Charging
        statusBox.style.background = "rgba(46,204,113,0.1)";
        statusDot.style.background = "#2ecc71";
        statusText.textContent = "ŁADOWANIE ↑";
        statusText.style.color = "#2ecc71";
        statusPower.style.color = "#2ecc71";
      } else {
        statusBox.style.background = "rgba(100,116,139,0.1)";
        statusDot.style.background = "#64748b";
        statusText.textContent = "STANDBY";
        statusText.style.color = "#64748b";
        statusPower.style.color = "#64748b";
      }
    }

    // Work mode & battery mode
    this._setText("v-work-mode-tab", this._s("sensor.work_mode") || this._s("select.goodwe_tryb_pracy_falownika") || "—");
    this._setText("v-batt-mode-tab", this._s("sensor.battery_mode") || "—");

    // DOD & charge rate
    const dod = this._n("number.goodwe_depth_of_discharge_on_grid");
    this._setText("v-batt-dod-tab", dod !== null ? `${dod}%` : "—%");
    this._setText("v-batt-charge-rate-tab", "18.5 A");

    // Daily charge/discharge
    const chargeToday = this._nm("battery_charge_today") || 0;
    const dischargeToday = this._nm("battery_discharge_today") || 0;
    this._setText("v-batt-charge-tab", `${chargeToday.toFixed(1)} kWh`);
    this._setText("v-batt-discharge-tab", `${dischargeToday.toFixed(1)} kWh`);

    // Cycles estimate (1 cycle = 10.2 kWh)
    const cycles = (dischargeToday / 10.2).toFixed(1);
    this._setText("v-batt-cycles-tab", cycles);

    // Efficiency (discharge/charge * 100)
    const efficiency = chargeToday > 0 ? Math.min(100, (dischargeToday / chargeToday) * 100) : 0;
    this._setText("v-batt-efficiency-tab", `${efficiency.toFixed(0)}%`);

    // Warnings
    const warning = this._s("sensor.warning_code") || "0";
    const warnEl = this.shadowRoot.getElementById("v-batt-warning-tab");
    if (warnEl) {
      if (warning === "0" || warning === "unavailable") { warnEl.textContent = "Brak"; warnEl.style.color = "#2ecc71"; }
      else { warnEl.textContent = `Kod: ${warning}`; warnEl.style.color = "#e74c3c"; }
    }

    // Arbitrage: RCE spread & prices
    const arbRceMin = this._n("sensor.rce_pse_min_today") ?? this._n("sensor.smartinghome_rce_min_today");
    const arbRceMax = this._n("sensor.rce_pse_max_today") ?? this._n("sensor.smartinghome_rce_max_today");
    if (arbRceMin !== null && arbRceMax !== null) {
      this._setText("v-arb-spread", `${(arbRceMax - arbRceMin).toFixed(2)} zł`);
      this._setText("v-arb-buy-price", `${(arbRceMin / 1000).toFixed(4)} zł`);
      this._setText("v-arb-sell-price", `${(arbRceMax / 1000).toFixed(4)} zł`);
    }

    // Premium/Free tier detection for arbitrage
    const arbCta = this.shadowRoot.getElementById("arb-premium-cta");
    const arbActive = this.shadowRoot.getElementById("arb-premium-active");
    if (arbCta && arbActive) {
      if (tier === "PRO" || tier === "ENTERPRISE") {
        arbCta.style.display = "none";
        arbActive.style.display = "block";
      } else {
        arbCta.style.display = "block";
        arbActive.style.display = "none";
      }
    }

    // PV Surplus — calculate from live values (pv - load)
    {
      const pvNow = this._nm("pv_power") || 0;
      const loadNow = this._nm("load_power") || 0;
      const surplus = pvNow - loadNow;
      this._setText("v-surplus", `${surplus > 0 ? '+' : ''}${Math.round(surplus)} W`);
      const surplusEl = this.shadowRoot.getElementById("v-surplus");
      if (surplusEl) surplusEl.style.color = surplus > 0 ? "#2ecc71" : "#e74c3c";
    }
    // Net Grid — calculate from daily import/export
    {
      const imp = this._nm("grid_import_today") || 0;
      const exp = this._nm("grid_export_today") || 0;
      const netG = exp - imp;
      this._setText("v-net-grid", `${netG > 0 ? '+' : ''}${netG.toFixed(1)} kWh`);
      const netEl = this.shadowRoot.getElementById("v-net-grid");
      if (netEl) netEl.style.color = netG >= 0 ? "#2ecc71" : "#e74c3c";
    }
    
    // AI Rec Tab — skip if AI content already loaded
    if (!this._aiHemsTabLoaded) {
      this._setText("v-hems-rec-hems", this._s("sensor.smartinghome_hems_recommendation") || "Brak danych z asystenta AI.");
    }
    
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

    // ══════ ENERGY TAB — Extended Data ══════

    // ROW 1: Daily Energy Balance KPIs
    const pvToday = this._nm("pv_today");
    const gridImpToday = this._nm("grid_import_today") || 0;
    const gridExpToday = this._nm("grid_export_today") || 0;
    const netToday = gridExpToday - gridImpToday;
    this._setText("v-en-pv-today", pvToday !== null ? pvToday.toFixed(1) : "—");
    this._setText("v-en-import-today", gridImpToday.toFixed(1));
    this._setText("v-en-export-today", gridExpToday.toFixed(1));
    const netTodayEl = this.shadowRoot.getElementById("v-en-net-today");
    if (netTodayEl) {
      netTodayEl.textContent = netToday.toFixed(1);
      netTodayEl.style.color = netToday >= 0 ? "#2ecc71" : "#e74c3c";
    }

    // ROW 2: Real-time Power KPIs
    const enPv = this._nm("pv_power") || 0;
    const enLoad = this._nm("load_power") || 0;
    const enSurplus = enPv - enLoad;
    const enBatt = this._nm("battery_power") || 0;
    const enSoc = this._nm("battery_soc") || 0;
    this._setText("v-en-pv-power", this._pw(enPv));
    this._setText("v-en-load-power", this._pw(enLoad));
    const surplusEl2 = this.shadowRoot.getElementById("v-en-surplus");
    if (surplusEl2) {
      surplusEl2.textContent = `${enSurplus > 0 ? '+' : ''}${Math.round(enSurplus)} W`;
      surplusEl2.style.color = enSurplus > 0 ? "#2ecc71" : "#e74c3c";
    }
    this._setText("v-en-batt-power", this._pw(Math.abs(enBatt)));
    const battDir = enBatt > 50 ? "ŁAD." : enBatt < -50 ? "ROZŁAD." : "STANDBY";
    this._setText("v-en-batt-info", `${Math.round(enSoc)}% · ${battDir}`);

    // ROW 3: Grid Extended — currents & powers per phase
    [1, 2, 3].forEach(i => {
      this._setText(`v-en-a${i}`, `${this._fm(`current_l${i}`, 1)} A`);
      this._setText(`v-en-p${i}`, `${this._fm(`power_l${i}`, 0)} W`);
    });

    // ROW 3: PV Strings
    for (let i = 1; i <= 4; i++) {
      const p = this._nm(`pv${i}_power`);
      if (i <= 2) {
        this._setText(`v-en-pv${i}-p`, p !== null ? this._pw(p) : "— W");
        this._setText(`v-en-pv${i}-v`, `${this._fm(`pv${i}_voltage`, 1)} V`);
        this._setText(`v-en-pv${i}-a`, `${this._fm(`pv${i}_current`, 1)} A`);
      } else {
        const box = this.shadowRoot.getElementById(`v-en-pv${i}-box`);
        if (box && p !== null && p > 0) {
          box.style.display = "";
          this._setText(`v-en-pv${i}-p`, this._pw(p));
        }
      }
    }
    this._setText("v-en-pv-total", this._pw(enPv));
    this._setText("v-en-pv-today2", pvToday !== null ? `${pvToday.toFixed(1)} kWh` : "— kWh");

    // Restore custom PV labels from settings
    const enPvLabels = (this._settings.pv_labels) || {};
    for (let i = 1; i <= 2; i++) {
      const lbl = enPvLabels[`pv${i}`];
      if (lbl) this._setText(`v-en-pv${i}-label`, lbl);
    }

    // ROW 4: Autarky & Self-consumption — computed from daily totals
    const enPvToday = pvToday ?? 0;
    const enImpToday = gridImpToday;
    const enExpToday = gridExpToday;
    const enHomeFromPvRaw = Math.max(0, enPvToday - enExpToday);
    const enLoadToday2 = this._nm("load_today") || 0;
    const enHomeFromPv = enLoadToday2 > 0 ? Math.min(enHomeFromPvRaw, enLoadToday2) : enHomeFromPvRaw;
    const autarky = (enPvToday + enImpToday) > 0 ? Math.min(100, (enPvToday / (enPvToday + enImpToday)) * 100) : 0;
    const selfCons = enPvToday > 0 ? Math.min(100, (enHomeFromPv / enPvToday) * 100) : 0;
    const enNetGrid = enExpToday - enImpToday;
    this._setText("v-en-autarky", `${Math.round(autarky)}%`);
    this._setText("v-en-selfcons", `${Math.round(selfCons)}%`);
    const autarkyBar = this.shadowRoot.getElementById("v-en-autarky-bar");
    if (autarkyBar) autarkyBar.style.width = `${Math.min(100, Math.max(0, autarky))}%`;
    const selfConsBar = this.shadowRoot.getElementById("v-en-selfcons-bar");
    if (selfConsBar) selfConsBar.style.width = `${Math.min(100, Math.max(0, selfCons))}%`;
    this._setText("v-en-home-from-pv", `${enHomeFromPv.toFixed(1)} kWh`);
    const enNetGridEl = this.shadowRoot.getElementById("v-en-net-grid");
    if (enNetGridEl) {
      enNetGridEl.textContent = `${enNetGrid > 0 ? '+' : ''}${enNetGrid.toFixed(1)} kWh`;
      enNetGridEl.style.color = enNetGrid >= 0 ? "#2ecc71" : "#e74c3c";
    }

    // ROW 1b: Day/Night breakdown in Energy tab
    {
      const totalAccumWs = this._energyDayWs + this._energyNightWs;
      const enLoadToday = this._nm("load_today") || parseFloat(this._hass?.states?.["sensor.today_load"]?.state) || 0;
      let enDayKwh = 0, enNightKwh = 0;
      if (totalAccumWs > 0 && enLoadToday > 0) {
        const dayRatio = this._energyDayWs / totalAccumWs;
        enDayKwh = enLoadToday * dayRatio;
        enNightKwh = enLoadToday * (1 - dayRatio);
      } else if (totalAccumWs > 0) {
        enDayKwh = this._energyDayWs / 3600000;
        enNightKwh = this._energyNightWs / 3600000;
      }

      // PV-based fallback: if accumulators missed daytime, use PV self-consumption
      if (enDayKwh === 0 && enHomeFromPv > 0 && enLoadToday > 0) {
        enDayKwh = Math.min(enLoadToday, enHomeFromPv);
        enNightKwh = Math.max(0, enLoadToday - enDayKwh);
      }

      const enTotalLoad = enDayKwh + enNightKwh;
      const enDayPct = enTotalLoad > 0 ? (enDayKwh / enTotalLoad) * 100 : 50;
      const enPvHomePct = enTotalLoad > 0 ? Math.min(100, (enHomeFromPv / enTotalLoad) * 100) : 0;

      this._setText("v-en-load-day", enDayKwh > 0 ? enDayKwh.toFixed(1) : "—");
      this._setText("v-en-load-night", enNightKwh > 0 ? enNightKwh.toFixed(1) : "—");
      this._setText("v-en-load-from-pv", enHomeFromPv > 0 ? enHomeFromPv.toFixed(1) : "—");
      this._setText("v-en-day-pct", enDayKwh > 0 ? `${enDayPct.toFixed(0)}%` : "—%");
      this._setText("v-en-pv-home-pct", enHomeFromPv > 0 ? `${enPvHomePct.toFixed(0)}%` : "—%");

      const dayBar = this.shadowRoot.getElementById("v-en-daybar");
      const nightBar = this.shadowRoot.getElementById("v-en-nightbar");
      if (dayBar) dayBar.style.width = `${enDayPct}%`;
      if (nightBar) nightBar.style.width = `${100 - enDayPct}%`;
      const pvHomeBar = this.shadowRoot.getElementById("v-en-pv-home-bar");
      if (pvHomeBar) pvHomeBar.style.width = `${enPvHomePct}%`;
    }
    // Forecast accuracy: actual / forecast * 100
    const fTodayVal = this._n("sensor.smartinghome_pv_forecast_today_total")
      ?? this._n("sensor.energy_production_today")
      ?? this._n("sensor.solcast_pv_forecast_today")
      ?? this._n("sensor.forecast_solar_energy_production_today");
    const fAccuracy = (fTodayVal && fTodayVal > 0 && enPvToday > 0) ? Math.min(200, (enPvToday / fTodayVal) * 100) : null;
    this._setText("v-en-forecast-accuracy", fAccuracy !== null ? `${Math.round(fAccuracy)}%` : "—%");

    // ROW 4: Inverter & Battery details
    this._setText("v-en-inv-p", this._pw(Math.abs(this._nm("inverter_power") || 0)));
    this._setText("v-en-inv-t", `${this._fm("inverter_temp", 1)} °C`);
    this._setText("v-en-batt-v", `${this._fm("battery_voltage", 1)} V`);
    this._setText("v-en-batt-a", `${this._fm("battery_current", 1)} A`);
    this._setText("v-en-batt-temp", `${this._fm("battery_temp", 1)} °C`);
    // Battery energy available: SOC% × capacity (default 10.2 kWh for GoodWe Lynx)
    const battCapacity = this._settings.battery_capacity_kwh || 10.2;
    const battEnergyAvail = enSoc > 0 ? (enSoc / 100) * battCapacity : 0;
    this._setText("v-en-batt-energy", `${battEnergyAvail.toFixed(1)} kWh`);
    // Battery runtime estimate: energy available / current load
    const enLoadKw = enLoad > 0 ? enLoad / 1000 : 0;
    if (battEnergyAvail > 0 && enLoadKw > 0) {
      const runtimeH = battEnergyAvail / enLoadKw;
      const rH = Math.floor(runtimeH);
      const rM = Math.round((runtimeH - rH) * 60);
      this._setText("v-en-batt-runtime", `${rH}h ${rM}min`);
    } else {
      this._setText("v-en-batt-runtime", enLoadKw === 0 ? "∞ (brak zużycia)" : "—");
    }
    const enChargeToday = this._nm("battery_charge_today") || 0;
    const enDischargeToday = this._nm("battery_discharge_today") || 0;
    this._setText("v-en-batt-charge", `${enChargeToday.toFixed(1)} kWh`);
    this._setText("v-en-batt-discharge", `${enDischargeToday.toFixed(1)} kWh`);

    // ROW 5: PV Forecast extended
    this._setText("v-en-forecast-now", this._pw(enPv));
    const fTodayKwh = fTodayVal ?? 0;
    const fRemainingKwh = Math.max(0, fTodayKwh - enPvToday);
    this._setText("v-en-forecast-remaining", fTodayKwh > 0 ? `${fRemainingKwh.toFixed(1)} kWh` : "— kWh");

    // ROW 6: Ecowitt local weather
    const ecoCard = this.shadowRoot.getElementById("en-ecowitt-card");
    const ecoTemp = this._n(this._m("local_temp"));
    if (ecoCard && ecoTemp !== null) {
      ecoCard.style.display = "";
      this._setText("v-en-eco-temp", `${ecoTemp.toFixed(1)}°C`);
      const ecoHum = this._n(this._m("local_humidity"));
      this._setText("v-en-eco-hum", ecoHum !== null ? `${Math.round(ecoHum)}%` : "—%");
      const ecoSolar = this._n(this._m("local_solar_radiation"));
      this._setText("v-en-eco-solar", ecoSolar !== null ? `${Math.round(ecoSolar)} W/m²` : "— W/m²");
      const ecoWind = this._n(this._m("local_wind_speed"));
      this._setText("v-en-eco-wind", ecoWind !== null ? `${ecoWind.toFixed(1)} km/h (${(ecoWind / 3.6).toFixed(1)} m/s)` : "—");
      const ecoRain = this._n(this._m("local_rain_rate"));
      this._setText("v-en-eco-rain", ecoRain !== null ? `${ecoRain.toFixed(1)} mm/h` : "— mm/h");
      const ecoPressure = this._n(this._m("local_pressure"));
      this._setText("v-en-eco-pressure", ecoPressure !== null ? `${Math.round(ecoPressure)} hPa` : "— hPa");
    }
  }

  /* ── History Tab ────────────────────────── */
  _histPeriod = "day";
  _histDate = new Date();
  _histCalendarDirty = true;

  _switchHistoryPeriod(p) {
    this._histPeriod = p;
    ["day","week","month","year"].forEach(k => {
      const btn = this.shadowRoot.getElementById(`hist-period-${k}`);
      if (btn) btn.classList.toggle("active", k === p);
    });
    // Show/hide ◀▶ buttons (only useful for 'day' since other periods use live utility meters)
    this.shadowRoot.querySelectorAll('.hist-nav-arrow').forEach(el => {
      el.style.display = p === 'day' ? 'inline-flex' : 'none';
    });
    this._histDate = new Date();
    this._histCalendarDirty = true;
    this._updateHistoryTab();
  }

  _histNavigate(dir) {
    if (this._histPeriod !== 'day') return;
    const d = this._histDate;
    d.setDate(d.getDate() + dir);
    // Don't go into the future
    const today = new Date();
    today.setHours(0,0,0,0);
    const sel = new Date(d); sel.setHours(0,0,0,0);
    if (sel > today) {
      this._histDate = new Date();
      this._histDate.setHours(0,0,0,0);
    }
    this._histCalendarDirty = true;
    this._updateHistoryTab();
  }

  _histToday() {
    this._histDate = new Date();
    this._histCalendarDirty = true;
    this._updateHistoryTab();
  }

  _isHistToday() {
    const d = this._histDate;
    const n = new Date();
    return d.getDate() === n.getDate() && d.getMonth() === n.getMonth() && d.getFullYear() === n.getFullYear();
  }

  _getHistSensorData() {
    const p = this._histPeriod;
    const suffixes = { day: "daily", week: "weekly", month: "monthly", year: "yearly" };
    const s = suffixes[p];

    // PV
    const pvVal = p === "day" ? (this._n("sensor.today_s_pv_generation") ?? 0) : (this._n(`sensor.pv_${s}`) ?? 0);

    // Grid (GoodWe swap: grid_export = YOUR import, grid_import = YOUR export)
    const impVal = this._n(`sensor.grid_export_${s}`) ?? 0;
    const expVal = this._n(`sensor.grid_import_${s}`) ?? 0;
    const selfUse = Math.max(0, pvVal - expVal);

    // Battery
    const batChg = p === "day" ? (this._n("sensor.today_battery_charge") ?? 0) : 0;
    const batDischg = p === "day" ? (this._n("sensor.today_battery_discharge") ?? 0) : 0;

    // G13 costs (also swapped in backend)
    const costVal = p === "day" ? (this._n("sensor.g13_export_revenue_today") ?? 0) : (this._n(`sensor.g13_export_revenue_${s}`) ?? 0);
    const revVal = p === "day" ? (this._n("sensor.g13_import_cost_today") ?? 0) : (this._n(`sensor.g13_import_cost_${s}`) ?? 0);
    const savVal = p === "day" ? (this._n("sensor.g13_self_consumption_savings_today") ?? 0) : (this._n(`sensor.g13_self_consumption_savings_${s}`) ?? 0);
    const balVal = revVal + savVal - costVal;

    // Efficiency
    const autarky = (pvVal + impVal) > 0 ? Math.min(100, (pvVal / (pvVal + impVal)) * 100) : 0;
    const selfCons = pvVal > 0 ? Math.min(100, (selfUse / pvVal) * 100) : 0;

    // String data
    const totalPv = this._nm('pv_power') || 1;
    const strings = [];
    for (let i = 1; i <= 4; i++) {
      const pw = this._nm(`pv${i}_power`);
      if (pw !== null || i <= 2) {
        const power = pw || 0;
        const ratio = totalPv > 0 ? power / totalPv : 0;
        const kwh = pvVal * ratio;
        const label = (this._settings.pv_labels || {})[`pv${i}`] || `PV${i}`;
        strings.push({ idx: i, label, power, ratio, kwh, pct: ratio * 100 });
      }
    }

    return { pvVal, impVal, expVal, selfUse, batChg, batDischg, costVal, revVal, savVal, balVal, autarky, selfCons, strings };
  }

  async _updateHistoryTab() {
    if (!this._hass || this._activeTab !== 'history') return;
    const p = this._histPeriod;
    const isToday = this._isHistToday();

    // For daily view on past dates → fetch from HA Recorder
    let d;
    if (p === 'day' && !isToday) {
      d = await this._fetchHistDayData(this._histDate);
    } else {
      d = this._getHistSensorData();
    }

    // Date label
    const now = this._histDate;
    const months = ["Styczeń","Luty","Marzec","Kwiecień","Maj","Czerwiec","Lipiec","Sierpień","Wrzesień","Październik","Listopad","Grudzień"];
    let dateStr = '';
    if (p === 'day') {
      dateStr = `${now.getDate()} ${months[now.getMonth()]} ${now.getFullYear()}`;
      if (!isToday) dateStr += ' ⏳';
    } else if (p === 'week') {
      const w = Math.ceil(((new Date() - new Date(new Date().getFullYear(),0,1)) / 86400000 + 1) / 7);
      dateStr = `Bieżący tydzień (${w})`;
    } else if (p === 'month') {
      dateStr = `${months[new Date().getMonth()]} ${new Date().getFullYear()}`;
    } else {
      dateStr = `${new Date().getFullYear()}`;
    }
    this._setText('hist-date-label', dateStr);

    // Show/hide ◀▶ buttons
    this.shadowRoot.querySelectorAll('.hist-nav-arrow').forEach(el => {
      el.style.display = p === 'day' ? 'inline-flex' : 'none';
    });

    // KPI Energy
    this._setText('hist-pv-val', `${d.pvVal.toFixed(1)} kWh`);
    this._setText('hist-import-val', `${d.impVal.toFixed(1)} kWh`);
    this._setText('hist-export-val', `${d.expVal.toFixed(1)} kWh`);
    this._setText('hist-selfuse-val', `${d.selfUse.toFixed(1)} kWh`);

    // KPI Finance
    this._setText('hist-cost-val', `${d.costVal.toFixed(2)} zł`);
    this._setText('hist-rev-val', `${d.revVal.toFixed(2)} zł`);
    this._setText('hist-sav-val', `${d.savVal.toFixed(2)} zł`);
    const balEl = this.shadowRoot.getElementById('hist-bal-val');
    if (balEl) {
      balEl.textContent = `${d.balVal >= 0 ? "+" : ""}${d.balVal.toFixed(2)} zł`;
      balEl.style.color = d.balVal >= 0 ? '#2ecc71' : '#e74c3c';
    }
    const balCard = this.shadowRoot.getElementById('hist-bal-card');
    if (balCard) {
      balCard.style.borderColor = d.balVal >= 0 ? 'rgba(46,204,113,0.3)' : 'rgba(231,76,60,0.3)';
      balCard.style.background = d.balVal >= 0 ? 'rgba(46,204,113,0.08)' : 'rgba(231,76,60,0.08)';
    }

    // Efficiency
    this._setText('hist-autarky-val', `${d.autarky.toFixed(0)}%`);
    this._setText('hist-selfcons-val', `${d.selfCons.toFixed(0)}%`);
    const autBar = this.shadowRoot.getElementById('hist-autarky-bar');
    const scBar = this.shadowRoot.getElementById('hist-selfcons-bar');
    if (autBar) autBar.style.width = `${d.autarky}%`;
    if (scBar) scBar.style.width = `${d.selfCons}%`;

    // Yield (kWh/kWp)
    const cfg = this._settings.pv_string_config || {};
    let totalWp = 0;
    for (let i = 1; i <= 4; i++) {
      const sc = cfg[`pv${i}`];
      if (sc && sc.substrings) {
        sc.substrings.forEach(sub => { totalWp += (sub.panel_count || 0) * (sub.panel_power || 0); });
      }
    }
    const installedKWp = totalWp / 1000;
    const yieldVal = installedKWp > 0 ? d.pvVal / installedKWp : 0;
    this._setText('hist-yield-val', installedKWp > 0 ? `${yieldVal.toFixed(2)} kWh/kWp` : '— (skonfiguruj stringi)');

    // Performance ratio
    const forecastToday = this._n('sensor.energy_production_today') ?? 0;
    const perfRatio = forecastToday > 0 && d.pvVal > 0 ? Math.min(100, (d.pvVal / forecastToday) * 100) : 0;
    this._setText('hist-perf-val', forecastToday > 0 ? `${perfRatio.toFixed(0)}%` : '—');
    const prBar = this.shadowRoot.getElementById('hist-perf-bar');
    if (prBar) prBar.style.width = `${Math.min(perfRatio, 100)}%`;

    // String table
    this._renderHistStrings(d);

    // Calendar heatmap — only re-render when dirty (not on 5s refresh cycle)
    if (this._histCalendarDirty) {
      this._histCalendarDirty = false;
      this._renderHistCalendar();
    }

    // Battery
    this._setText('hist-bat-chg', `${d.batChg.toFixed(1)} kWh`);
    this._setText('hist-bat-dischg', `${d.batDischg.toFixed(1)} kWh`);
    const cycles = (d.batChg > 0 || d.batDischg > 0) ? ((d.batChg + d.batDischg) / 2 / 10.2).toFixed(2) : '—';
    this._setText('hist-bat-cycles', typeof cycles === 'string' ? cycles : `~${cycles}`);

    // Comparison
    this._renderHistComparison(d);
  }

  async _fetchHistDayData(date) {
    // Fetch historical data for a specific day from HA Recorder
    const start = new Date(date); start.setHours(0,0,0,0);
    const end = new Date(date); end.setHours(23,59,59,999);
    const defaultData = { pvVal:0, impVal:0, expVal:0, selfUse:0, batChg:0, batDischg:0, costVal:0, revVal:0, savVal:0, balVal:0, autarky:0, selfCons:0, strings:[] };

    try {
      const stats = await this._hass.callWS({
        type: 'recorder/statistics_during_period',
        start_time: start.toISOString(),
        end_time: end.toISOString(),
        statistic_ids: [
          'sensor.today_s_pv_generation',
          'sensor.grid_export_daily',
          'sensor.grid_import_daily',
          'sensor.today_battery_charge',
          'sensor.today_battery_discharge',
        ],
        period: 'day',
        types: ['change'],
      });

      // Extract values from statistics response
      const getChange = (id) => {
        const arr = stats[id];
        if (!arr || arr.length === 0) return 0;
        return arr.reduce((sum, s) => sum + (s.change || 0), 0);
      };

      const pvVal = getChange('sensor.today_s_pv_generation');
      const impVal = getChange('sensor.grid_export_daily'); // GoodWe swap
      const expVal = getChange('sensor.grid_import_daily'); // GoodWe swap
      const selfUse = Math.max(0, pvVal - expVal);
      const batChg = getChange('sensor.today_battery_charge');
      const batDischg = getChange('sensor.today_battery_discharge');

      // Estimate costs using current tariff rates
      const g13Rates = { import: 0.6271, export: 0.3573 }; // PLN/kWh fallback
      const costVal = impVal * g13Rates.import;
      const revVal = expVal * g13Rates.export;
      const savVal = selfUse * g13Rates.import;
      const balVal = revVal + savVal - costVal;

      const autarky = (pvVal + impVal) > 0 ? Math.min(100, (pvVal / (pvVal + impVal)) * 100) : 0;
      const selfCons = pvVal > 0 ? Math.min(100, (selfUse / pvVal) * 100) : 0;

      // String ratios not available for past days, use even split
      const strings = [];
      const totalPv = this._nm('pv_power') || 1;
      for (let i = 1; i <= 2; i++) {
        const pw = this._nm(`pv${i}_power`) || 0;
        const ratio = totalPv > 0 ? pw / totalPv : 0.5;
        const label = (this._settings.pv_labels || {})[`pv${i}`] || `PV${i}`;
        strings.push({ idx: i, label, power: pw, ratio, kwh: pvVal * ratio, pct: ratio * 100 });
      }

      return { pvVal, impVal, expVal, selfUse, batChg, batDischg, costVal, revVal, savVal, balVal, autarky, selfCons, strings };
    } catch (e) {
      console.warn('Smarting HOME: Recorder query failed, falling back to live data', e);
      return this._getHistSensorData();
    }
  }

  _renderHistStrings(d) {
    const tbody = this.shadowRoot.getElementById('hist-string-tbody');
    if (!tbody) return;
    let html = '';
    d.strings.forEach(s => {
      html += `<tr style="border-bottom:1px solid rgba(255,255,255,0.04)">
        <td style="padding:8px 10px; font-size:12px; font-weight:600; color:#f7b731">${s.label}</td>
        <td style="padding:8px 10px; font-size:12px; color:#fff; text-align:right">${this._pw(s.power)}</td>
        <td style="padding:8px 10px; font-size:12px; color:#00d4ff; text-align:right">${s.kwh.toFixed(1)} kWh</td>
        <td style="padding:8px 10px; font-size:12px; text-align:right">
          <div style="display:flex; align-items:center; gap:6px; justify-content:flex-end">
            <div style="width:60px; height:6px; background:rgba(255,255,255,0.08); border-radius:3px; overflow:hidden">
              <div style="height:100%; width:${s.pct}%; background:linear-gradient(90deg, #f7b731, #00d4ff); border-radius:3px"></div>
            </div>
            <span style="color:#94a3b8; min-width:36px">${s.pct.toFixed(1)}%</span>
          </div>
        </td>
      </tr>`;
    });
    // Total row
    const totalPower = d.strings.reduce((a, s) => a + s.power, 0);
    html += `<tr style="border-top:2px solid rgba(0,212,255,0.2); background:rgba(0,212,255,0.04)">
      <td style="padding:8px 10px; font-size:12px; font-weight:800; color:#00d4ff">Σ SUMA</td>
      <td style="padding:8px 10px; font-size:12px; font-weight:800; color:#fff; text-align:right">${this._pw(totalPower)}</td>
      <td style="padding:8px 10px; font-size:12px; font-weight:800; color:#00d4ff; text-align:right">${d.pvVal.toFixed(1)} kWh</td>
      <td style="padding:8px 10px; font-size:12px; font-weight:800; color:#94a3b8; text-align:right">100%</td>
    </tr>`;
    tbody.innerHTML = html;
  }

  async _renderHistCalendar() {
    const container = this.shadowRoot.getElementById('hist-calendar-grid');
    if (!container || !this._hass) return;
    const p = this._histPeriod;
    const now = new Date();
    let cells = [];

    try {
      if (p === 'year') {
        // Query monthly stats for the current year
        const yearStart = new Date(now.getFullYear(), 0, 1);
        const stats = await this._hass.callWS({
          type: 'recorder/statistics_during_period',
          start_time: yearStart.toISOString(),
          end_time: now.toISOString(),
          statistic_ids: ['sensor.today_s_pv_generation'],
          period: 'month',
          types: ['change'],
        });

        const arr = stats['sensor.today_s_pv_generation'] || [];
        const monthlyKWh = new Array(12).fill(0);
        arr.forEach(s => {
          const d = new Date(s.start);
          monthlyKWh[d.getMonth()] = s.change || 0;
        });
        const maxKWh = Math.max(...monthlyKWh, 1);

        const monthNames = ['Sty','Lut','Mar','Kwi','Maj','Cze','Lip','Sie','Wrz','Paź','Lis','Gru'];
        for (let m = 0; m < 12; m++) {
          const isPast = m <= now.getMonth();
          const factor = isPast ? monthlyKWh[m] / maxKWh : 0;
          const kwh = monthlyKWh[m];
          cells.push({ label: monthNames[m], factor, isPast, kwh });
        }
        container.style.gridTemplateColumns = 'repeat(6, 1fr)';
      } else {
        // Query daily stats for last 30 days
        const start = new Date(now);
        start.setDate(start.getDate() - 29);
        start.setHours(0,0,0,0);

        const stats = await this._hass.callWS({
          type: 'recorder/statistics_during_period',
          start_time: start.toISOString(),
          end_time: now.toISOString(),
          statistic_ids: ['sensor.today_s_pv_generation'],
          period: 'day',
          types: ['change'],
        });

        const arr = stats['sensor.today_s_pv_generation'] || [];
        const dailyMap = {};
        arr.forEach(s => {
          const d = new Date(s.start);
          const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
          dailyMap[key] = s.change || 0;
        });
        const allVals = Object.values(dailyMap);
        const maxKWh = Math.max(...allVals, 1);

        for (let i = 29; i >= 0; i--) {
          const dt = new Date(now);
          dt.setDate(dt.getDate() - i);
          const key = `${dt.getFullYear()}-${dt.getMonth()}-${dt.getDate()}`;
          const kwh = dailyMap[key] || 0;
          const factor = kwh / maxKWh;
          const isToday = i === 0;
          const isSelected = p === 'day' && dt.getDate() === this._histDate.getDate() && dt.getMonth() === this._histDate.getMonth();
          cells.push({ label: String(dt.getDate()), factor, isPast: !isToday, isToday, isSelected, kwh });
        }
        container.style.gridTemplateColumns = 'repeat(10, 1fr)';
      }
    } catch(e) {
      console.warn('Smarting HOME: Calendar stats query failed', e);
      // Fallback: show empty grid
      for (let i = 29; i >= 0; i--) {
        const dt = new Date(now); dt.setDate(dt.getDate() - i);
        cells.push({ label: String(dt.getDate()), factor: 0, isPast: true, isToday: i === 0, kwh: 0 });
      }
      container.style.gridTemplateColumns = 'repeat(10, 1fr)';
    }

    container.innerHTML = cells.map(c => {
      const intensity = Math.max(0, Math.min(1, c.factor));
      let bg;
      if (intensity === 0) bg = 'rgba(255,255,255,0.03)';
      else if (intensity < 0.3) bg = `rgba(231,76,60,${0.15 + intensity * 0.4})`;
      else if (intensity < 0.6) bg = `rgba(247,183,49,${0.15 + intensity * 0.4})`;
      else bg = `rgba(46,204,113,${0.15 + intensity * 0.5})`;
      const border = c.isToday ? 'border:2px solid #00d4ff;' : c.isSelected ? 'border:2px solid #f7b731;' : '';
      const kwhTip = c.kwh !== undefined ? ` | ${c.kwh.toFixed(1)} kWh` : '';
      return `<div class="hist-cal-cell" style="background:${bg}; ${border}; cursor:pointer" title="${c.label}: ${(intensity * 100).toFixed(0)}%${kwhTip}" onclick="this.getRootNode().host._histCalendarClick('${c.label}')">
        <span>${c.label}</span>
      </div>`;
    }).join('');
  }

  _histCalendarClick(label) {
    // Allow clicking a calendar day to navigate to it (daily view only)
    if (this._histPeriod !== 'day') return;
    const dayNum = parseInt(label, 10);
    if (isNaN(dayNum)) return;
    // Find the date within last 30 days matching this day number
    const now = new Date();
    for (let i = 0; i < 30; i++) {
      const dt = new Date(now);
      dt.setDate(dt.getDate() - i);
      if (dt.getDate() === dayNum) {
        this._histDate = dt;
        this._updateHistoryTab();
        return;
      }
    }
  }

  _renderHistComparison(d) {
    const ct = this.shadowRoot.getElementById('hist-compare-body');
    if (!ct) return;

    // Previous period data — use multiplier estimation
    const multiplier = { day: 365, week: 52, month: 12, year: 1 };
    const p = this._histPeriod;
    const m = multiplier[p];

    // Compute estimated annual values
    const yearlyPV = d.pvVal * m;
    const yearlyImport = d.impVal * m;
    const yearlyExport = d.expVal * m;
    const yearlyBalance = d.balVal * m;

    const rows = [
      { label: '☀️ Produkcja PV', curr: d.pvVal, unit: 'kWh', yearly: yearlyPV, color: '#f7b731' },
      { label: '🔌 Import', curr: d.impVal, unit: 'kWh', yearly: yearlyImport, color: '#e74c3c' },
      { label: '📤 Eksport', curr: d.expVal, unit: 'kWh', yearly: yearlyExport, color: '#2ecc71' },
      { label: '💵 Bilans', curr: d.balVal, unit: 'zł', yearly: yearlyBalance, color: d.balVal >= 0 ? '#2ecc71' : '#e74c3c' },
    ];

    ct.innerHTML = rows.map(r => {
      return `<div class="hist-compare-row">
        <span class="hist-cmp-label">${r.label}</span>
        <span class="hist-cmp-curr" style="color:${r.color}">${r.curr.toFixed(1)} ${r.unit}</span>
        <span class="hist-cmp-arrow">→</span>
        <span class="hist-cmp-yearly" style="color:#94a3b8">
          ~${r.yearly.toFixed(0)} ${r.unit}/rok
        </span>
      </div>`;
    }).join('');
  }

  _exportHistoryData(format) {
    const d = this._getHistSensorData();
    const p = this._histPeriod;
    const periodLabels = { day: 'dzien', week: 'tydzien', month: 'miesiac', year: 'rok' };
    const now = this._histDate;
    const dateStr = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
    const filename = `smartinghome_historia_${periodLabels[p]}_${dateStr}`;

    if (format === 'csv') {
      const header = 'Metryka;Wartość;Jednostka';
      const rows = [
        `Okres;${p};—`,
        `Data;${dateStr};—`,
        `Produkcja PV;${d.pvVal.toFixed(2)};kWh`,
        `Import z sieci;${d.impVal.toFixed(2)};kWh`,
        `Eksport do sieci;${d.expVal.toFixed(2)};kWh`,
        `Autokonsumpcja;${d.selfUse.toFixed(2)};kWh`,
        `Koszt importu;${d.costVal.toFixed(2)};PLN`,
        `Przychod eksportu;${d.revVal.toFixed(2)};PLN`,
        `Oszczednosci;${d.savVal.toFixed(2)};PLN`,
        `Bilans netto;${d.balVal.toFixed(2)};PLN`,
        `Autarkia;${d.autarky.toFixed(1)};%`,
        `Autokonsumpcja %;${d.selfCons.toFixed(1)};%`,
        `Ladowanie baterii;${d.batChg.toFixed(2)};kWh`,
        `Rozladowanie baterii;${d.batDischg.toFixed(2)};kWh`,
      ];
      d.strings.forEach(s => {
        rows.push(`${s.label} moc;${s.power.toFixed(0)};W`);
        rows.push(`${s.label} produkcja;${s.kwh.toFixed(2)};kWh`);
        rows.push(`${s.label} udzial;${s.pct.toFixed(1)};%`);
      });
      const csv = [header, ...rows].join('\n');
      const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `${filename}.csv`; a.click();
      URL.revokeObjectURL(url);
    } else {
      const json = {
        meta: { period: p, date: dateStr, generated: new Date().toISOString(), source: 'Smarting HOME' },
        energy: { pv_kwh: d.pvVal, import_kwh: d.impVal, export_kwh: d.expVal, self_use_kwh: d.selfUse },
        financial: { import_cost_pln: d.costVal, export_revenue_pln: d.revVal, savings_pln: d.savVal, balance_pln: d.balVal },
        efficiency: { autarky_pct: d.autarky, self_consumption_pct: d.selfCons },
        battery: { charge_kwh: d.batChg, discharge_kwh: d.batDischg },
        strings: d.strings.map(s => ({ label: s.label, power_w: s.power, production_kwh: s.kwh, share_pct: s.pct })),
      };
      const blob = new Blob([JSON.stringify(json, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `${filename}.json`; a.click();
      URL.revokeObjectURL(url);
    }
    const st = this.shadowRoot.getElementById('hist-export-status');
    if (st) { st.textContent = `✅ Wyeksportowano ${format.toUpperCase()}`; setTimeout(() => { st.textContent = ''; }, 3000); }
  }

  /* ── Render ─────────────────────────────── */
  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; height: 100%; color-scheme: dark; }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        select, option { color-scheme: dark; background-color: #1e293b; color: #e0e6ed; }

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
        .tabs { display: flex; gap: 2px; padding: 4px 20px; background: rgba(0,0,0,0.25); overflow-x: auto; flex-wrap: nowrap; -webkit-overflow-scrolling: touch; scrollbar-width: none; }
        .tabs::-webkit-scrollbar { display: none; }
        .tab-btn {
          padding: 7px 14px; border: none; background: transparent;
          color: #64748b; font-size: 12px; font-weight: 500; cursor: pointer;
          border-radius: 7px; transition: all 0.2s; white-space: nowrap;
        }
        .tab-btn:hover { background: rgba(255,255,255,0.05); color: #a0aec0; }
        .tab-btn.active { background: rgba(0,212,255,0.1); color: #00d4ff; }

        /* ── Content ── */
        .tab-content { display: none; padding: 12px 6px; }
        .tab-content.active { display: block; }

        /* ═══════════════════════════════════════ */
        /* ═══  POWER FLOW — ORTHOGONAL LAYOUT ═══ */
        /* ═══════════════════════════════════════ */
        .flow-wrapper {
          position: relative;
          width: 100%; max-width: 100%; margin: 0 auto; padding: 0 12px;
          min-height: 520px;
          box-sizing: border-box;
        }
        .flow-svg-bg {
          position: absolute; top: 0; left: 0; width: 100%; height: 100%;
          pointer-events: none; z-index: 0;
        }
        .flow-nodes {
          position: relative; z-index: 1;
          display: grid;
          grid-template-columns: 1.5fr 0.3fr 1.5fr;
          grid-template-rows: auto auto auto;
          gap: 12px; min-height: 540px;
        }

        /* Node boxes */
        .node {
          width: 100%; max-width: 440px; box-sizing: border-box;
          background: rgba(20, 30, 48, 0.4);
          backdrop-filter: blur(12px);
          -webkit-backdrop-filter: blur(12px);
          border: 1px solid rgba(255,255,255,0.1);
          box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
          border-radius: 16px;
          padding: 18px;
          position: relative;
          transition: all 0.4s ease;
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

        /* Corner layout: PV top-left, Home top-right, Inv center-up, Batt bottom-left, Grid bottom-right */
        .pv-area { grid-column: 1; grid-row: 1; display: flex; justify-content: flex-start; }
        .home-area { grid-column: 3; grid-row: 1; display: flex; justify-content: flex-end; }
        .inv-area { grid-column: 2; grid-row: 1 / 4; display: flex; flex-direction: column; align-items: center; justify-content: center; align-self: center; }
        .batt-area { grid-column: 1; grid-row: 2 / 4; align-self: center; display: flex; justify-content: flex-start; }
        .grid-area { grid-column: 3; grid-row: 2 / 4; align-self: center; display: flex; justify-content: flex-end; }
        .summary-area { grid-column: 1 / 4; grid-row: 4; }

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
          width: 150px; height: 140px;
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
          max-width: 100px; max-height: 80px;
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
        .fl-dot.pv-charge { fill: #00aaff; color: #00aaff; }
        @keyframes pvPulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }

        /* --- BATTERY GRAPHIC --- */
        .battery-graphic {
          width: 38px;
          border: 2px solid rgba(255,255,255,0.25);
          border-radius: 8px;
          padding: 5px;
          display: flex;
          flex-direction: column-reverse;
          gap: 3px;
          position: relative;
          background: rgba(0,0,0,0.25);
          flex-shrink: 0;
          align-self: stretch;
          margin-top: 6px;
        }
        .battery-graphic::before {
          content: '';
          position: absolute;
          top: -7px;
          left: 50%;
          transform: translateX(-50%);
          width: 16px;
          height: 5px;
          background: rgba(255,255,255,0.25);
          border-radius: 3px 3px 0 0;
        }
        .batt-bar {
          width: 100%;
          height: 8px;
          background: rgba(255,255,255,0.06);
          border-radius: 2px;
          transition: background-color 0.5s ease, box-shadow 0.3s ease;
        }
        @keyframes batt-pulse-up {
          0%   { opacity: 0.35; }
          50%  { opacity: 1; box-shadow: 0 0 8px currentColor; }
          100% { opacity: 0.35; }
        }
        @keyframes batt-pulse-down {
          0%   { opacity: 1; box-shadow: 0 0 8px currentColor; }
          50%  { opacity: 0.35; }
          100% { opacity: 1; box-shadow: 0 0 8px currentColor; }
        }
        @keyframes etaPulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.55; }
        }
        @keyframes etaPulseFast {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }

        /* General SOC progress bars (used in tabs) */
        .soc-bar { width: 100%; height: 100%; background: rgba(255,255,255,0.05); border-radius: 4px; overflow: hidden; }
        .soc-fill { height: 100%; transition: width 0.5s, background-color 0.5s; background-color: #2ecc71; }

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
        .sidebar-toggle {
          background: none; border: none; color: #a0aec0; cursor: pointer;
          padding: 6px; border-radius: 8px; transition: all 0.2s;
          display: flex; align-items: center; justify-content: center;
        }
        .sidebar-toggle:hover { background: rgba(255,255,255,0.08); color: #fff; }
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

        /* ── Responsive grid helpers ── */
        .g4 { display: grid; gap: 10px; grid-template-columns: repeat(4, 1fr); }
        .g3 { display: grid; gap: 10px; grid-template-columns: repeat(3, 1fr); }
        .g2-1fr { display: grid; gap: 10px; grid-template-columns: 1fr 1fr; }

        /* ── Top bar container ── */
        .top-bar { display: flex; align-items: stretch; background: rgba(255,255,255,0.03); border-bottom: 1px solid rgba(255,255,255,0.06); flex-wrap: wrap; }
        .top-left { flex: 2; min-width: 0; }
        .top-center { flex: 1; display: flex; align-items: center; justify-content: center; gap: 8px; padding: 4px 10px; border-left: 1px solid rgba(255,255,255,0.06); min-width: 200px; overflow: hidden; }
        .top-right { display: flex; flex-direction: column; justify-content: center; align-items: flex-end; gap: 6px; padding: 6px 14px; border-left: 1px solid rgba(255,255,255,0.06); flex-shrink: 0; }

        /* ═══ TABLET LANDSCAPE (≤1024px) ═══ */
        @media (max-width: 1024px) {
          .top-bar { flex-wrap: wrap; }
          .top-left { flex: 1 1 100%; order: 1; }
          .top-center { flex: 1 1 100%; order: 2; min-width: 200px; border-left: none; border-top: 1px solid rgba(255,255,255,0.06); padding: 6px 12px; overflow: hidden; }
          .top-right { order: 3; flex: 1 1 100%; flex-direction: row; justify-content: flex-end; padding: 6px 12px; border-left: none; border-top: 1px solid rgba(255,255,255,0.06); }
          .tabs { padding: 4px 12px; }
          .flow-wrapper { min-height: 440px; }
          .flow-nodes { min-height: 440px; gap: 8px; }
          .node { padding: 14px; }
          .g4 { grid-template-columns: repeat(2, 1fr); }
        }

        /* ═══ TABLET PORTRAIT (≤768px) ═══ */
        @media (max-width: 768px) {
          .header h1 { font-size: 15px; }
          .tabs { padding: 4px 10px; }
          .tab-btn { padding: 6px 10px; font-size: 11px; }
          .tab-content { padding: 10px 10px; }
          .flow-wrapper { min-height: 380px; }
          .flow-nodes { min-height: 380px; gap: 6px; grid-template-columns: 1fr 1fr; grid-template-rows: auto; }
          .pv-area { grid-column: 1; grid-row: 1; }
          .home-area { grid-column: 2; grid-row: 1; }
          .inv-area { grid-column: 1 / 3; grid-row: 2; flex-direction: row; gap: 12px; }
          .batt-area { grid-column: 1; grid-row: 3; }
          .grid-area { grid-column: 2; grid-row: 3; }
          .summary-area { grid-column: 1 / 3; }
          .flow-svg-bg { display: none; }
          .node { padding: 12px; }
          .node-title { font-size: 9px; }
          .card { padding: 14px; border-radius: 12px; }
          .card-title { font-size: 10px; margin-bottom: 8px; }
          .gc-2 { grid-template-columns: 1fr; }
          .gc-3 { grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
          .g4 { grid-template-columns: repeat(2, 1fr); }
          .g3 { grid-template-columns: 1fr 1fr; }
          .summary-bar { gap: 10px; padding: 10px 12px; flex-wrap: wrap; justify-content: space-around; }
          .summary-item .si-val { font-size: 14px; }
          .recommendation { padding: 10px; font-size: 12px; }
          .dr .lb { font-size: 11px; }
          .dr .vl { font-size: 12px; }
          .actions { grid-template-columns: 1fr; }
        }

        /* ═══ PHONE (≤480px) ═══ */
        @media (max-width: 480px) {
          .top-center { min-width: unset; flex: 1 1 100%; justify-content: space-between; padding: 4px 8px; gap: 4px; overflow: hidden; }
          .top-center svg { max-width: 140px; }
          .sun-arc-wrap { max-width: 140px; height: 60px; }
          .sun-side-left { min-width: 55px !important; }
          .sun-side-left .sun-clock { font-size: 22px !important; }
          .sun-side-right { min-width: 60px !important; }
          .sun-side-right .sun-pct { font-size: 18px !important; }
          .top-right { flex: 1 1 100%; flex-direction: row; justify-content: center; }
          .header h1 { font-size: 14px; }
          .tabs { padding: 4px 8px; }
          .tab-btn { padding: 5px 8px; font-size: 10px; }
          .tab-content { padding: 8px 6px; }
          .flow-wrapper { min-height: auto; overflow: visible; }
          .flow-nodes { grid-template-columns: 1fr; min-height: auto; gap: 14px; }
          .pv-area    { grid-column: 1; grid-row: 1; }
          .home-area  { grid-column: 1; grid-row: 2; }
          .inv-area   { grid-column: 1; grid-row: 3; flex-direction: column; align-items: center; }
          .batt-area  { grid-column: 1; grid-row: 4; }
          .grid-area  { grid-column: 1; grid-row: 5; }
          .summary-area { grid-column: 1; grid-row: 6; }
          .inv-box { width: 110px !important; height: 100px !important; }
          .inv-box img { max-width: 80px !important; }
          .node { padding: 10px; border-radius: 12px; }
          .node-title { font-size: 9px; letter-spacing: 0.5px; }
          .node-value { font-size: 22px; }
          .node img { max-width: 70px; max-height: 60px; }
          .pv-string { padding: 6px 8px; }
          .pv-string .pv-val { font-size: 13px; }
          .card { padding: 10px; border-radius: 10px; }
          .card-title { font-size: 9px; }
          .g4 { grid-template-columns: 1fr 1fr; gap: 6px; }
          .g3 { grid-template-columns: 1fr; gap: 6px; }
          .g2-1fr { grid-template-columns: 1fr; gap: 6px; }
          .gc-3 { grid-template-columns: 1fr; }
          .summary-bar { flex-direction: column; gap: 6px; padding: 8px; }
          .summary-item { display: flex; justify-content: space-between; width: 100%; }
          .summary-item .si-label { text-align: left; }
          .summary-item .si-val { font-size: 13px; text-align: right; }
          .dr { padding: 6px 0; }
          .dr .lb { font-size: 10px; }
          .dr .vl { font-size: 11px; }
          .recommendation { padding: 8px; font-size: 11px; line-height: 1.5; }
          .fullscreen-btn { display: none; }
          .settings-field input { font-size: 12px; padding: 8px 10px; }
          .save-btn { font-size: 12px; padding: 8px 18px; }
        }

        /* ── Custom Modal ────────────── */
        .sh-modal-overlay {
          display: none; position: fixed; inset: 0; z-index: 10000;
          background: rgba(0,0,0,0.65); backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
          align-items: center; justify-content: center;
        }
        .sh-modal-overlay.active { display: flex; animation: shModalFadeIn 0.2s ease; }
        @keyframes shModalFadeIn { from { opacity: 0; } to { opacity: 1; } }
        @keyframes shModalSlideIn { from { transform: translateY(-20px) scale(0.95); opacity: 0; } to { transform: translateY(0) scale(1); opacity: 1; } }
        .sh-modal {
          background: linear-gradient(145deg, #1a2744, #0f1d35);
          border: 1px solid rgba(0,212,255,0.15); border-radius: 18px;
          padding: 28px; min-width: 380px; max-width: 560px; width: 90vw;
          box-shadow: 0 25px 60px rgba(0,0,0,0.5), 0 0 40px rgba(0,212,255,0.08);
          animation: shModalSlideIn 0.25s ease;
          max-height: 85vh; overflow-y: auto;
        }
        .sh-modal-title {
          font-size: 16px; font-weight: 800; color: #fff; margin-bottom: 18px;
          display: flex; align-items: center; gap: 8px;
        }
        .sh-modal-label { font-size: 11px; color: #94a3b8; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
        .sh-modal-input {
          width: 100%; padding: 10px 14px; border-radius: 10px;
          background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.1);
          color: #fff; font-size: 14px; outline: none; box-sizing: border-box;
          transition: border-color 0.2s;
        }
        .sh-modal-input:focus { border-color: rgba(0,212,255,0.5); }
        .sh-modal-select {
          width: 100%; padding: 8px 12px; border-radius: 10px;
          background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.1);
          color: #fff; font-size: 12px; outline: none; box-sizing: border-box;
          appearance: none; cursor: pointer;
        }
        .sh-modal-select option { background: #1a2744; color: #fff; }
        .sh-modal-actions {
          display: flex; gap: 10px; justify-content: flex-end; margin-top: 20px;
        }
        .sh-modal-btn {
          padding: 9px 22px; border-radius: 10px; font-size: 13px; font-weight: 700;
          border: 1px solid rgba(255,255,255,0.12); background: rgba(255,255,255,0.06);
          color: #94a3b8; cursor: pointer; transition: all 0.2s;
        }
        .sh-modal-btn:hover { background: rgba(255,255,255,0.1); color: #fff; }
        .sh-modal-btn.primary {
          background: linear-gradient(135deg, #00d4ff, #0099cc);
          color: #0a1628; border-color: transparent;
        }
        .sh-modal-btn.primary:hover { filter: brightness(1.15); }
        .sh-modal-btn.danger {
          background: rgba(231,76,60,0.15); color: #e74c3c; border-color: rgba(231,76,60,0.2);
        }
        .sh-modal-btn.danger:hover { background: rgba(231,76,60,0.25); }
        .sh-modal-divider { border: none; border-top: 1px solid rgba(255,255,255,0.06); margin: 14px 0; }
        .substring-card {
          background: rgba(247,183,49,0.06); border: 1px solid rgba(247,183,49,0.12);
          border-radius: 12px; padding: 12px; margin-bottom: 8px;
        }
        .substring-card .sc-header {
          display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;
        }
        .substring-card .sc-title { font-size: 11px; font-weight: 700; color: #f7b731; }
        .substring-card .sc-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
        .substring-card .sc-field label { font-size: 9px; color: #64748b; display: block; margin-bottom: 3px; }
        .substring-card .sc-field input,
        .substring-card .sc-field select {
          width: 100%; padding: 6px 8px; border-radius: 8px;
          background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.08);
          color: #fff; font-size: 12px; outline: none; box-sizing: border-box;
        }
        .substring-card .sc-field select { appearance: none; cursor: pointer; }
        .substring-card .sc-field select option { background: #1a2744; }
        .pv-config-btn {
          display: inline-flex; align-items: center; justify-content: center;
          width: 18px; height: 18px; border-radius: 6px; font-size: 10px;
          background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.08);
          color: #64748b; cursor: pointer; transition: all 0.2s; margin-left: 4px;
          vertical-align: middle; line-height: 1;
        }
        .pv-config-btn:hover { background: rgba(0,212,255,0.15); color: #00d4ff; border-color: rgba(0,212,255,0.2); }
        .sh-modal-preview {
          background: rgba(0,212,255,0.04); border: 1px solid rgba(0,212,255,0.1);
          border-radius: 10px; padding: 12px; margin-top: 12px;
        }
        .sh-modal-preview .prev-title { font-size: 10px; color: #00d4ff; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
        .sh-modal-preview .prev-row { display: flex; justify-content: space-between; padding: 3px 0; font-size: 11px; }
        .sh-modal-preview .prev-row .prev-name { color: #f7b731; }
        .sh-modal-preview .prev-row .prev-vals { color: #94a3b8; }
        .sh-toggle { position: relative; display: inline-block; width: 36px; height: 20px; }
        .sh-toggle input { opacity: 0; width: 0; height: 0; }
        .sh-toggle-slider {
          position: absolute; cursor: pointer; inset: 0; background: rgba(255,255,255,0.1);
          border-radius: 20px; transition: 0.3s;
        }
        .sh-toggle-slider:before {
          content: ''; position: absolute; height: 14px; width: 14px;
          left: 3px; bottom: 3px; background: #94a3b8;
          border-radius: 50%; transition: 0.3s;
        }
        .sh-toggle input:checked + .sh-toggle-slider { background: rgba(0,212,255,0.3); }
        .sh-toggle input:checked + .sh-toggle-slider:before { transform: translateX(16px); background: #00d4ff; }

        /* ═══════════════════════════════════════ */
        /* ═══  HEMS ARBITRAGE DASHBOARD      ═══ */
        /* ═══════════════════════════════════════ */
        .hems-header-strip {
          display: grid; grid-template-columns: 1fr auto;
          align-items: center; gap: 12px;
          padding: 16px 18px;
          background: linear-gradient(135deg, rgba(0,212,255,0.08), rgba(0,230,118,0.05));
          border: 1px solid rgba(0,212,255,0.12); border-radius: 16px;
          margin-bottom: 14px;
        }
        .hems-header-strip .hems-h-title {
          font-size: 15px; font-weight: 800; color: #fff;
          display: flex; align-items: center; gap: 8px;
        }
        .hems-header-strip .hems-h-badges {
          display: flex; gap: 6px; flex-wrap: wrap;
        }
        .hems-h-badge {
          padding: 3px 10px; border-radius: 20px; font-size: 10px;
          font-weight: 700; letter-spacing: 0.3px;
        }
        .hems-h-badge.mode { background: rgba(0,212,255,0.15); color: #00d4ff; border: 1px solid rgba(0,212,255,0.2); }
        .hems-h-badge.active { background: rgba(46,204,113,0.15); color: #2ecc71; border: 1px solid rgba(46,204,113,0.2); }
        .hems-h-badge.tier { background: rgba(247,183,49,0.15); color: #f7b731; border: 1px solid rgba(247,183,49,0.2); }
        .hems-kpis {
          display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 14px;
        }
        .hems-kpi {
          background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06);
          border-radius: 12px; padding: 12px; text-align: center;
        }
        .hems-kpi .kpi-label { font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }
        .hems-kpi .kpi-val { font-size: 22px; font-weight: 800; margin-top: 4px; }
        .hems-layer {
          margin-bottom: 10px; border: 1px solid rgba(255,255,255,0.06);
          border-radius: 14px; overflow: hidden;
          background: rgba(255,255,255,0.015);
        }
        .hems-layer-header {
          display: flex; align-items: center; justify-content: space-between;
          padding: 12px 16px; cursor: pointer;
          background: rgba(255,255,255,0.03);
          transition: background 0.2s;
          user-select: none;
        }
        .hems-layer-header:hover { background: rgba(255,255,255,0.06); }
        .hems-layer-header .hl-left { display: flex; align-items: center; gap: 8px; }
        .hems-layer-header .hl-tag {
          font-size: 10px; font-weight: 700; padding: 3px 10px; border-radius: 6px;
          text-transform: uppercase; letter-spacing: 0.5px;
        }
        .hems-layer-header .hl-name { font-size: 15px; font-weight: 700; color: #e2e8f0; }
        .hems-layer-header .hl-chevron {
          font-size: 14px; color: #64748b; transition: transform 0.3s;
        }
        .hems-layer.collapsed .hl-chevron { transform: rotate(-90deg); }
        .hems-layer-body {
          display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
          gap: 8px; padding: 12px;
          max-height: 2000px; overflow: hidden;
          transition: max-height 0.4s ease, padding 0.3s, opacity 0.3s;
          opacity: 1;
        }
        .hems-layer.collapsed .hems-layer-body {
          max-height: 0; padding: 0 12px; opacity: 0;
        }
        .hems-auto-card {
          background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08);
          border-radius: 12px; padding: 14px;
          transition: all 0.3s; position: relative; overflow: hidden;
        }
        .hems-auto-card:hover {
          background: rgba(255,255,255,0.06);
          border-color: rgba(255,255,255,0.12);
        }
        .hems-auto-card .hac-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
        .hems-auto-card .hac-icon { font-size: 20px; }
        .hems-auto-card .hac-name { font-size: 13px; font-weight: 700; color: #e2e8f0; flex: 1; margin-left: 8px; }
        .hems-auto-card .hac-status {
          font-size: 10px; font-weight: 700; padding: 3px 10px;
          border-radius: 10px; text-transform: uppercase; letter-spacing: 0.3px;
        }
        .hems-auto-card .hac-status.on { background: rgba(46,204,113,0.15); color: #2ecc71; }
        .hems-auto-card .hac-status.wait { background: rgba(247,183,49,0.15); color: #f7b731; }
        .hems-auto-card .hac-status.off { background: rgba(100,116,139,0.15); color: #64748b; }
        .hems-auto-card .hac-desc { font-size: 12px; color: #94a3b8; line-height: 1.4; margin-bottom: 8px; }
        .hems-auto-card .hac-sensors {
          display: grid; grid-template-columns: 1fr 1fr; gap: 4px;
          font-size: 12px;
        }
        .hems-auto-card .hac-sensors .hs-label { color: #64748b; }
        .hems-auto-card .hac-sensors .hs-val { color: #a0aec0; font-weight: 600; text-align: right; }
        .hems-auto-card.is-active {
          border-color: rgba(46,204,113,0.25);
          background: rgba(46,204,113,0.04);
        }
        .hems-auto-card.is-active::before {
          content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
          background: linear-gradient(90deg, #2ecc71, #00d4ff);
        }
        @keyframes hemsPulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(46,204,113,0.2); }
          50% { box-shadow: 0 0 12px 2px rgba(46,204,113,0.15); }
        }
        .hems-auto-card.is-active { animation: hemsPulse 3s ease-in-out infinite; }

        @media (max-width: 768px) {
          .hems-kpis { grid-template-columns: repeat(2, 1fr); }
          .hems-layer-body { grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); }
          .hems-header-strip { grid-template-columns: 1fr; }
        }
        @media (max-width: 480px) {
          .hems-kpis { grid-template-columns: 1fr 1fr; gap: 6px; }
          .hems-kpi .kpi-val { font-size: 16px; }
          .hems-layer-body { grid-template-columns: 1fr; }
          .hems-auto-card .hac-sensors { grid-template-columns: 1fr 1fr; }
        }

        /* ═══  AUTOPILOT TAB STYLES  ═══ */
        .ap-header-strip {
          display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 10px;
          padding: 14px 18px; margin-bottom: 16px;
          background: linear-gradient(135deg, rgba(124,58,237,0.12), rgba(0,212,255,0.08));
          border: 1px solid rgba(124,58,237,0.25);
          border-radius: 16px;
        }
        .ap-header-strip .ap-h-title {
          font-size: 16px; font-weight: 800; color: #fff;
          background: linear-gradient(90deg, #7c3aed, #00d4ff);
          -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .ap-badges { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
        .ap-badge {
          padding: 4px 12px; border-radius: 20px; font-size: 10px; font-weight: 700;
          text-transform: uppercase; letter-spacing: 0.5px;
        }
        .ap-badge.active { background: rgba(46,204,113,0.15); color: #2ecc71; }
        .ap-badge.provider { background: rgba(0,212,255,0.12); color: #00d4ff; }
        .ap-badge.tier { background: rgba(247,183,49,0.12); color: #f7b731; }

        .ap-strategies {
          display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
          gap: 10px; margin-bottom: 16px;
        }
        .ap-strategy-card {
          position: relative; padding: 14px 16px; border-radius: 14px;
          border: 1px solid rgba(255,255,255,0.06);
          background: rgba(255,255,255,0.03); cursor: pointer;
          transition: all 0.3s ease;
        }
        .ap-strategy-card:hover {
          background: rgba(255,255,255,0.06); border-color: rgba(255,255,255,0.12);
          transform: translateY(-2px);
        }
        .ap-strategy-card.ap-active {
          border-color: rgba(124,58,237,0.5);
          background: linear-gradient(135deg, rgba(124,58,237,0.08), rgba(0,212,255,0.05));
          animation: apPulse 3s ease-in-out infinite;
        }
        .ap-strategy-card.ap-active::before {
          content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
          background: linear-gradient(90deg, #7c3aed, #00d4ff);
          border-radius: 14px 14px 0 0;
        }
        @keyframes apPulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(124,58,237,0.15); }
          50% { box-shadow: 0 0 16px 3px rgba(124,58,237,0.1); }
        }
        .ap-sc-icon { font-size: 22px; margin-bottom: 6px; }
        .ap-sc-name { font-size: 14px; font-weight: 700; color: #fff; margin-bottom: 4px; }
        .ap-sc-desc { font-size: 11px; color: #94a3b8; line-height: 1.4; }
        .ap-sc-savings {
          margin-top: 8px; padding: 6px 10px; border-radius: 8px;
          background: rgba(46,204,113,0.08); font-size: 12px; font-weight: 700;
          color: #2ecc71; text-align: center;
        }

        .ap-timeline {
          display: flex; gap: 1px; height: 60px; border-radius: 10px;
          overflow: hidden; margin: 10px 0;
          background: rgba(255,255,255,0.03);
        }
        .ap-th {
          flex: 1; display: flex; flex-direction: column; align-items: center;
          justify-content: flex-end; position: relative; min-width: 0;
          transition: all 0.2s;
        }
        .ap-th:hover { background: rgba(255,255,255,0.05); }
        .ap-th-bar {
          width: 100%; border-radius: 3px 3px 0 0;
          transition: height 0.5s ease;
        }
        .ap-th-label {
          font-size: 9px; color: #64748b; padding: 2px 0;
        }
        .ap-th.charge .ap-th-bar { background: linear-gradient(180deg, #2ecc71, #27ae60); }
        .ap-th.discharge .ap-th-bar { background: linear-gradient(180deg, #e74c3c, #c0392b); }
        .ap-th.sell .ap-th-bar { background: linear-gradient(180deg, #f7b731, #e67e22); }
        .ap-th.hold .ap-th-bar { background: linear-gradient(180deg, #64748b, #475569); }
        .ap-th.current { background: rgba(0,212,255,0.1); }
        .ap-th.current .ap-th-label { color: #00d4ff; font-weight: 700; }

        .ap-estimation {
          display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
          gap: 8px; margin: 12px 0;
        }
        .ap-est-card {
          padding: 12px; border-radius: 12px; text-align: center;
          border: 1px solid rgba(255,255,255,0.04);
        }
        .ap-est-label { font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 0.8px; }
        .ap-est-val { font-size: 22px; font-weight: 800; margin-top: 4px; }

        .ap-ai-analysis {
          padding: 14px; border-radius: 12px;
          background: rgba(124,58,237,0.05); border: 1px solid rgba(124,58,237,0.15);
          font-size: 13px; color: #cbd5e1; line-height: 1.6;
          max-height: 800px; overflow-y: auto;
        }
        .ap-ai-analysis h2 { font-size: 16px; color: #fff; margin: 12px 0 6px; }
        .ap-ai-analysis h3 { font-size: 14px; color: #a78bfa; margin: 8px 0 4px; }
        .ap-ai-analysis strong { color: #fff; }
        .ap-ai-analysis table { width: 100%; border-collapse: collapse; font-size: 12px; margin: 8px 0; }
        .ap-ai-analysis th, .ap-ai-analysis td {
          padding: 4px 8px; border: 1px solid rgba(255,255,255,0.08); text-align: left;
        }
        .ap-ai-analysis th { background: rgba(255,255,255,0.04); color: #94a3b8; }

        @media (max-width: 768px) {
          .ap-strategies { grid-template-columns: repeat(2, 1fr); }
          .ap-header-strip { grid-template-columns: 1fr; }
          .ap-estimation { grid-template-columns: repeat(2, 1fr); }
        }
        @media (max-width: 480px) {
          .ap-strategies { grid-template-columns: 1fr; }
          .ap-estimation { grid-template-columns: 1fr 1fr; }
          .ap-timeline { height: 45px; }
        }

        /* ── History Tab ── */
        .hist-control-bar {
          display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
          padding: 10px 14px; margin-bottom: 12px;
          background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06);
          border-radius: 14px;
        }
        .hist-period-btn {
          padding: 6px 14px; border: 1px solid rgba(255,255,255,0.1);
          background: transparent; color: #64748b; font-size: 11px; font-weight: 600;
          cursor: pointer; border-radius: 8px; transition: all 0.2s;
        }
        .hist-period-btn:hover { background: rgba(255,255,255,0.05); color: #a0aec0; }
        .hist-period-btn.active { background: rgba(0,212,255,0.12); color: #00d4ff; border-color: rgba(0,212,255,0.3); }
        .hist-nav-btn {
          padding: 6px 10px; border: 1px solid rgba(255,255,255,0.08);
          background: transparent; color: #94a3b8; font-size: 14px;
          cursor: pointer; border-radius: 8px; transition: all 0.2s;
        }
        .hist-nav-btn:hover { background: rgba(255,255,255,0.05); color: #fff; }
        .hist-export-btn {
          padding: 5px 12px; border: 1px solid rgba(0,212,255,0.2);
          background: rgba(0,212,255,0.06); color: #00d4ff; font-size: 10px;
          font-weight: 600; cursor: pointer; border-radius: 8px; transition: all 0.2s;
        }
        .hist-export-btn:hover { background: rgba(0,212,255,0.15); }
        .hist-kpi-grid {
          display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 12px;
        }
        .hist-kpi {
          background: rgba(20, 30, 48, 0.5); border: 1px solid rgba(255,255,255,0.06);
          border-radius: 14px; padding: 14px; text-align: center;
          transition: all 0.3s;
        }
        .hist-kpi:hover { border-color: rgba(0,212,255,0.2); transform: translateY(-2px); }
        .hist-kpi-label {
          font-size: 9px; color: #64748b; text-transform: uppercase;
          letter-spacing: 0.5px; margin-bottom: 6px;
        }
        .hist-kpi-val {
          font-size: 20px; font-weight: 800; color: #fff; line-height: 1.1;
        }
        .hist-section {
          background: rgba(20, 30, 48, 0.4); border: 1px solid rgba(255,255,255,0.06);
          border-radius: 14px; padding: 16px; margin-bottom: 12px;
        }
        .hist-section-title {
          font-size: 13px; font-weight: 700; color: #e0e6ed; margin-bottom: 12px;
          display: flex; align-items: center; gap: 8px;
        }
        .hist-progress-wrap {
          display: flex; align-items: center; gap: 10px; margin-bottom: 10px;
        }
        .hist-progress-label {
          font-size: 11px; color: #94a3b8; min-width: 120px;
        }
        .hist-progress-bar-bg {
          flex: 1; height: 10px; background: rgba(255,255,255,0.06);
          border-radius: 5px; overflow: hidden;
        }
        .hist-progress-bar-fill {
          height: 100%; border-radius: 5px; transition: width 0.6s ease;
        }
        .hist-progress-val {
          font-size: 13px; font-weight: 700; min-width: 48px; text-align: right;
        }
        .hist-cal-cell {
          border-radius: 6px; padding: 6px 2px; text-align: center;
          font-size: 9px; font-weight: 600; color: #e0e6ed;
          cursor: default; transition: transform 0.2s;
          min-height: 32px; display: flex; align-items: center; justify-content: center;
        }
        .hist-cal-cell:hover { transform: scale(1.1); z-index: 1; }
        .hist-compare-row {
          display: flex; align-items: center; gap: 10px;
          padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.04);
        }
        .hist-cmp-label { font-size: 12px; color: #94a3b8; min-width: 120px; }
        .hist-cmp-curr { font-size: 14px; font-weight: 700; min-width: 100px; }
        .hist-cmp-arrow { font-size: 12px; color: #64748b; }
        .hist-cmp-yearly { font-size: 11px; }
        @media (max-width: 768px) {
          .hist-kpi-grid { grid-template-columns: repeat(2, 1fr); }
          .hist-control-bar { flex-direction: column; align-items: stretch; }
          .hist-compare-row { flex-wrap: wrap; }
          .hist-cmp-label { min-width: 100%; }
        }
        @media (max-width: 480px) {
          .hist-kpi-grid { grid-template-columns: 1fr 1fr; gap: 6px; }
          .hist-kpi-val { font-size: 16px; }
        }

        /* ── Sub-Meters ── */
        .submeter-section {
          margin-top: 12px; padding: 16px; border-radius: 16px;
          background: rgba(20, 30, 48, 0.4);
          border: 1px solid rgba(255,255,255,0.06);
        }
        .submeter-header {
          display: flex; align-items: center; justify-content: space-between;
          margin-bottom: 12px;
        }
        .submeter-header-title {
          font-size: 13px; font-weight: 700; color: #e0e6ed;
          display: flex; align-items: center; gap: 8px;
        }
        .submeter-grid {
          display: flex;
          gap: 10px;
          overflow-x: auto;
          padding-bottom: 6px;
          scroll-snap-type: x mandatory;
          -webkit-overflow-scrolling: touch;
        }
        .submeter-grid::-webkit-scrollbar { height: 4px; }
        .submeter-grid::-webkit-scrollbar-track { background: rgba(255,255,255,0.03); border-radius: 2px; }
        .submeter-grid::-webkit-scrollbar-thumb { background: rgba(0,212,255,0.2); border-radius: 2px; }
        .submeter-card {
          background: rgba(255,255,255,0.03);
          border: 1px solid rgba(255,255,255,0.06);
          border-radius: 14px; padding: 14px 12px;
          text-align: center; transition: all 0.3s ease;
          position: relative;
          min-width: 140px; max-width: 180px;
          flex-shrink: 0;
          scroll-snap-align: start;
        }
        .submeter-card:hover {
          border-color: rgba(0,212,255,0.2);
          background: rgba(255,255,255,0.05);
          transform: translateY(-2px);
        }
        .submeter-icon { font-size: 22px; margin-bottom: 4px; }
        .submeter-name {
          font-size: 10px; color: #94a3b8; text-transform: uppercase;
          letter-spacing: 0.5px; margin-bottom: 6px;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .submeter-power {
          font-size: 20px; font-weight: 800; color: #fff;
          line-height: 1.2; margin-bottom: 4px;
        }
        .submeter-energy {
          font-size: 11px; color: #64748b; font-weight: 600;
        }
        .submeter-bar-bg {
          margin-top: 8px; height: 4px; border-radius: 2px;
          background: rgba(255,255,255,0.06); overflow: hidden;
        }
        .submeter-bar-fill {
          height: 100%; border-radius: 2px;
          background: linear-gradient(90deg, #00d4ff, #2ecc71);
          transition: width 0.6s ease;
        }
        .submeter-empty {
          text-align: center; padding: 20px; color: #64748b; font-size: 11px;
        }
        /* Sub-meters settings */
        .sm-list-item {
          display: flex; align-items: center; gap: 10px;
          padding: 10px 12px; background: rgba(255,255,255,0.03);
          border: 1px solid rgba(255,255,255,0.06);
          border-radius: 10px; margin-bottom: 6px;
          transition: all 0.2s;
        }
        .sm-list-item:hover { border-color: rgba(0,212,255,0.15); }
        .sm-list-icon { font-size: 20px; flex-shrink: 0; }
        .sm-list-info { flex: 1; min-width: 0; }
        .sm-list-name { font-size: 12px; font-weight: 700; color: #fff; }
        .sm-list-entity { font-size: 10px; color: #64748b; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .sm-list-actions { display: flex; gap: 4px; flex-shrink: 0; }
        .sm-list-btn {
          padding: 4px 8px; border: 1px solid rgba(255,255,255,0.08);
          background: transparent; color: #94a3b8; font-size: 11px;
          cursor: pointer; border-radius: 6px; transition: all 0.2s;
        }
        .sm-list-btn:hover { background: rgba(255,255,255,0.06); color: #fff; }
        .sm-list-btn.danger:hover { background: rgba(231,76,60,0.15); color: #e74c3c; }
        .sm-suggest-list {
          max-height: 150px; overflow-y: auto; border: 1px solid rgba(255,255,255,0.1);
          border-radius: 8px; background: rgba(15,23,42,0.95); margin-top: 4px;
        }
        .sm-suggest-item {
          padding: 6px 10px; font-size: 11px; color: #cbd5e1; cursor: pointer;
          border-bottom: 1px solid rgba(255,255,255,0.04);
        }
        .sm-suggest-item:hover { background: rgba(0,212,255,0.1); color: #fff; }
        @media (max-width: 768px) {
          .submeter-card { min-width: 120px; }
        }
        @media (max-width: 480px) {
          .submeter-card { min-width: 110px; }
          .submeter-power { font-size: 16px; }
        }
      </style>

      <div class="panel-container">
        <!-- Top Row: Logo+Tabs (left) + Sun Widget (mid) + Buttons (right) -->
        <div class="top-bar">
          <!-- Left: Header + Tabs -->
          <div class="top-left">
            <div class="header" style="position:relative; border-bottom:none">
              <div class="header-left">
                <button class="sidebar-toggle" onclick="this.getRootNode().host._toggleHaSidebar()" title="Menu">
                  <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z"/></svg>
                </button>
                <span style="font-size:22px">⚡</span>
                <h1>Smarting HOME</h1>
              </div>
            </div>
            <div class="tabs" style="border-bottom:none">
              <button class="tab-btn active" data-tab="overview" onclick="this.getRootNode().host._switchTab('overview')">📊 Przegląd</button>
              <button class="tab-btn" data-tab="energy" onclick="this.getRootNode().host._switchTab('energy')">⚡ Energia</button>
              <button class="tab-btn" data-tab="tariff" onclick="this.getRootNode().host._switchTab('tariff')">💰 Taryfy & RCE</button>
              <button class="tab-btn" data-tab="battery" onclick="this.getRootNode().host._switchTab('battery')">🔋 Bateria</button>
              <button class="tab-btn" data-tab="hems" onclick="this.getRootNode().host._switchTab('hems')">🤖 HEMS</button>
              <button class="tab-btn" data-tab="roi" onclick="this.getRootNode().host._switchTab('roi')">📈 Opłacalność</button>
              <button class="tab-btn" data-tab="winter" onclick="this.getRootNode().host._switchTab('winter')">❄️ Zima na plusie</button>
              <button class="tab-btn" data-tab="wind" onclick="this.getRootNode().host._switchTab('wind')">🌬️ Wiatr</button>
              <button class="tab-btn" data-tab="history" onclick="this.getRootNode().host._switchTab('history')">📅 Historia</button>
              <button class="tab-btn" data-tab="autopilot" onclick="this.getRootNode().host._switchTab('autopilot')" id="tab-btn-autopilot" style="display:none">🧠 Autopilot</button>
            </div>
          </div>
          <!-- Center: Sun Widget (compact) -->
          <div class="top-center" style="justify-content:space-between; gap:6px; padding:4px 22px">
            <div class="sun-side-left" style="text-align:left; min-width:65px; flex-shrink:1">
              <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.8px" id="ov-date">—</div>
              <div class="sun-clock" style="font-size:28px; font-weight:900; color:#fff; letter-spacing:-1px; line-height:1" id="ov-clock">--:--</div>
              <div style="font-size:10px; color:#94a3b8; margin-top:2px" id="ov-day-name">—</div>
            </div>
            <div class="sun-arc-wrap" style="position:relative; flex:1 1 auto; min-width:0; max-width:240px; height:78px">
              <svg viewBox="0 0 200 105" style="width:100%; height:100%">
                <defs>
                  <clipPath id="sun-clip"><rect id="ov-sun-clip-rect" x="0" y="-20" width="0" height="150" /></clipPath>
                </defs>
                <path d="M 10,98 Q 100,-15 190,98" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="1.5" />
                <path id="ov-sun-arc" d="M 10,98 Q 100,-15 190,98" fill="none" stroke="#f7b731" stroke-width="2.5" clip-path="url(#sun-clip)" />
                <line x1="5" y1="98" x2="195" y2="98" stroke="rgba(255,255,255,0.08)" stroke-width="0.5" />
                <circle id="ov-sun-dot" cx="100" cy="50" r="7" fill="#f7b731" style="filter:drop-shadow(0 0 8px #f7b731); transition: all 1s ease" />
              </svg>
              <div style="position:absolute; bottom:0; left:2px; font-size:9px; color:#f7b731">🌅 <span id="ov-sunrise">—</span></div>
              <div style="position:absolute; bottom:0; right:2px; font-size:9px; color:#e67e22; text-align:right">🌇 <span id="ov-sunset">—</span></div>
            </div>
            <div class="sun-side-right" style="text-align:right; min-width:70px; flex-shrink:1">
              <div style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px" id="ov-status-label">Dzień</div>
              <div class="sun-pct" style="font-size:24px; font-weight:800; color:#f7b731" id="ov-daylight-pct">—%</div>
              <div style="font-size:10px; color:#94a3b8" id="ov-daylight-left">—</div>
              <div style="font-size:8px; color:#8b9dc3; margin-top:1px; display:none" id="ov-moon-phase-name"></div>
            </div>
          </div>
          <!-- Right: Actions -->
          <div class="top-right">
            <div style="display:flex; align-items:center; gap:6px">
              <span class="badge free" id="v-license">FREE</span>
              <button class="gear-btn" title="Ustawienia" onclick="this.getRootNode().host._switchTab('settings')">⚙️</button>
            </div>
            <button class="fullscreen-btn" onclick="this.getRootNode().host._toggleFullscreen()">⊞ Pełny ekran</button>
          </div>
        </div>

        <!-- ═══════ TAB: OVERVIEW ═══════ -->
        <div class="tab-content active" data-tab="overview">
          <div class="flow-wrapper">
            <!-- ORTHOGONAL SVG OVERLAY -->
            <svg class="flow-svg-bg" viewBox="0 0 700 480">
              <!-- PV (top-left) → Inverter (center) -->
              <path class="fl-line" id="line-pv-inv" d="M 80,90 H 350 V 180" />
              <g id="fl-pv-inv" class="fl-dot solar" style="display:none">
                <circle r="5" />
                <animateMotion id="anim-pv-inv" dur="2.5s" repeatCount="indefinite" path="M 80,90 H 350 V 180" />
              </g>
              <!-- PV → Inverter (Bolt) -->
              <g id="fl-pv-inv-bolt" class="fl-dot pv-charge" style="display:none">
                <circle r="7" style="animation: pvPulse 1.2s ease-in-out infinite" />
                <text text-anchor="middle" dominant-baseline="central" font-size="10" fill="#fff" style="font-weight:900; filter:drop-shadow(0 0 4px #00aaff)">⚡</text>
                <animateMotion id="anim-pv-inv-bolt" dur="2.5s" repeatCount="indefinite" path="M 80,90 H 350 V 180" />
              </g>
              <!-- Inverter → Home (top-right) -->
              <path class="fl-line" id="line-inv-load" d="M 350,180 V 90 H 620" />
              <g id="fl-inv-load" class="fl-dot load-flow" style="display:none">
                <circle r="5" />
                <animateMotion id="anim-inv-load" dur="2.5s" repeatCount="indefinite" path="M 350,180 V 90 H 620" />
              </g>
              <!-- Battery → Inverter -->
              <path class="fl-line" id="line-batt-inv" d="M 80,340 V 410 H 350 V 260" />
              <g id="fl-batt-inv" class="fl-dot batt-discharge" style="display:none">
                <circle r="5" />
                <animateMotion id="anim-batt-inv" dur="2.5s" repeatCount="indefinite" path="M 80,340 V 410 H 350 V 260" />
              </g>
              <!-- Inverter -> Battery -->
              <g id="fl-inv-batt" class="fl-dot batt-charge" style="display:none">
                <circle r="5" />
                <animateMotion id="anim-inv-batt" dur="2.5s" repeatCount="indefinite" path="M 350,260 V 410 H 80 V 340" />
              </g>
              <g id="fl-inv-batt-pv" class="fl-dot pv-charge" style="display:none">
                <circle r="7" style="animation: pvPulse 1.2s ease-in-out infinite" />
                <text text-anchor="middle" dominant-baseline="central" font-size="10" fill="#fff" style="font-weight:900; filter:drop-shadow(0 0 4px #00aaff)">⚡</text>
                <animateMotion id="anim-inv-batt-pv" dur="2.5s" repeatCount="indefinite" path="M 350,260 V 410 H 80 V 340" />
              </g>
              <!-- Grid → Inverter -->
              <path class="fl-line" id="line-grid-inv" d="M 620,340 V 410 H 350 V 260" />
              <g id="fl-grid-inv" class="fl-dot grid-in" style="display:none">
                <circle r="5" />
                <animateMotion id="anim-grid-inv" dur="2.5s" repeatCount="indefinite" path="M 620,340 V 410 H 350 V 260" />
              </g>
              <g id="fl-inv-grid" class="fl-dot grid-out" style="display:none">
                <circle r="5" />
                <animateMotion id="anim-inv-grid" dur="2.5s" repeatCount="indefinite" path="M 350,260 V 410 H 620 V 340" />
              </g>
            </svg>

            <!-- FLOW NODES GRID -->
            <div class="flow-nodes">
              <!-- ☀️ PV AREA (top left) -->
              <div class="pv-area">
                <div class="node" id="pv-node" style="border-color: rgba(247,183,49,0.2); transition: border-color 0.5s, box-shadow 0.5s">
                  <div style="display:flex; gap:10px">
                    <div style="flex:1; min-width:0">
                      <div class="node-title">☀️ Produkcja PV</div>
                      <div class="node-big" style="color:#f7b731" id="v-pv">— W</div>
                      <div class="node-sub" id="v-pv-today">— kWh dziś</div>
                      <div style="font-size:10px; color:#f7b731; margin-top:2px; font-weight:600" id="v-pv-total-kwh"></div>
                      <div class="pv-strings" style="display:flex; gap:6px; flex-wrap:wrap; margin-top:10px">
                        <div class="pv-string" id="pv1-box"><div class="pv-name" id="pv1-label" onclick="this.getRootNode().host._editPvLabel(1)" style="cursor:pointer" title="Kliknij aby zmienić nazwę"><span id="pv1-label-text">PV1</span><span class="pv-config-btn" onclick="event.stopPropagation(); this.getRootNode().host._openPvStringConfig(1)" title="Konfiguracja stringa">⚙️</span></div><div class="pv-val" id="v-pv1-p">—</div><div class="pv-detail"><span id="v-pv1-v">— V</span> · <span id="v-pv1-a">— A</span></div><div style="font-size:9px; color:#94a3b8; margin-top:2px" id="v-pv1-kwh"></div></div>
                        <div class="pv-string" id="pv2-box"><div class="pv-name" id="pv2-label" onclick="this.getRootNode().host._editPvLabel(2)" style="cursor:pointer" title="Kliknij aby zmienić nazwę"><span id="pv2-label-text">PV2</span><span class="pv-config-btn" onclick="event.stopPropagation(); this.getRootNode().host._openPvStringConfig(2)" title="Konfiguracja stringa">⚙️</span></div><div class="pv-val" id="v-pv2-p">—</div><div class="pv-detail"><span id="v-pv2-v">— V</span> · <span id="v-pv2-a">— A</span></div><div style="font-size:9px; color:#94a3b8; margin-top:2px" id="v-pv2-kwh"></div></div>
                        <div class="pv-string" id="pv3-box" style="display:none"><div class="pv-name" id="pv3-label" onclick="this.getRootNode().host._editPvLabel(3)" style="cursor:pointer"><span id="pv3-label-text">PV3</span><span class="pv-config-btn" onclick="event.stopPropagation(); this.getRootNode().host._openPvStringConfig(3)" title="Konfiguracja stringa">⚙️</span></div><div class="pv-val" id="v-pv3-p">—</div><div class="pv-detail"><span id="v-pv3-v">—</span></div></div>
                        <div class="pv-string" id="pv4-box" style="display:none"><div class="pv-name" id="pv4-label" onclick="this.getRootNode().host._editPvLabel(4)" style="cursor:pointer"><span id="pv4-label-text">PV4</span><span class="pv-config-btn" onclick="event.stopPropagation(); this.getRootNode().host._openPvStringConfig(4)" title="Konfiguracja stringa">⚙️</span></div><div class="pv-val" id="v-pv4-p">—</div><div class="pv-detail"><span id="v-pv4-v">—</span></div></div>
                      </div>
                    </div>
                    <div id="pv-eco-sidebar" style="display:none; flex-shrink:0; width:110px; border-left:1px solid rgba(255,255,255,0.06); padding-left:10px">
                      <div style="font-size:8px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:6px">🌦️ Stacja</div>
                      <div style="display:flex; flex-direction:column; gap:5px">
                        <div>
                          <div style="font-size:8px; color:#64748b">☀️ Nasłoneczn.</div>
                          <div style="font-size:13px; font-weight:800; color:#f7b731" id="pv-eco-solar">—</div>
                        </div>
                        <div>
                          <div style="font-size:8px; color:#64748b">🔆 UV</div>
                          <div style="font-size:11px; font-weight:700" id="pv-eco-uv" style="color:#2ecc71">—</div>
                        </div>
                        <div>
                          <div style="font-size:8px; color:#64748b">🌡️ Temp.</div>
                          <div style="font-size:11px; font-weight:700; color:#fff" id="pv-eco-temp">—</div>
                        </div>
                        <div>
                          <div style="font-size:8px; color:#64748b">💨 Wiatr</div>
                          <div style="font-size:11px; font-weight:700; color:#00d4ff" id="pv-eco-wind">—</div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <!-- 🏠 ZUZYCIE (top right) -->
              <div class="home-area">
                <div class="node" id="home-node" style="border-color: rgba(46,204,113,0.2); transition: border-color 0.5s, box-shadow 0.5s">
                  <div style="display:flex; align-items:flex-start; gap:10px">
                    <div style="flex:1; min-width:0">
                      <div class="node-title">🏠 Zużycie</div>
                      <div class="node-big" id="v-load">— W</div>
                      <div style="font-size:11px; color:#2ecc71; margin-top:3px; font-weight:600" id="v-load-today"></div>
                    </div>
                    <div style="flex-shrink:0; text-align:right">
                      <img id="v-home-img" src="https://smartinghome.pl/wp-content/uploads/2026/03/grafika-domu.png" alt="Dom" style="width:90px; max-height:70px; object-fit:contain; opacity:0.85; border-radius:8px" />
                      <div class="node-detail" style="margin-top:4px; text-align:right; font-size:10px">
                        <span id="v-load-l1">L1: — W</span>
                        <span style="color:rgba(255,255,255,0.15)"> · </span>
                        <span id="v-load-l2">L2: — W</span>
                        <span style="color:rgba(255,255,255,0.15)"> · </span>
                        <span id="v-load-l3">L3: — W</span>
                      </div>
                    </div>
                  </div>
                  <!-- Day/Night breakdown -->
                  <div id="v-load-breakdown" style="margin-top:8px; padding-top:6px; border-top:1px solid rgba(255,255,255,0.06)">
                    <div style="display:flex; gap:6px; margin-bottom:4px">
                      <div style="flex:1; background:rgba(247,183,49,0.1); border-radius:8px; padding:5px 4px; text-align:center">
                        <div style="font-size:7px; color:#f7b731; text-transform:uppercase; letter-spacing:0.5px">☀️ Dzień</div>
                        <div style="font-size:14px; font-weight:800; color:#f7b731" id="v-load-day">—</div>
                        <div style="font-size:8px; color:#94a3b8">kWh</div>
                      </div>
                      <div style="flex:1; background:rgba(139,157,195,0.1); border-radius:8px; padding:5px 4px; text-align:center">
                        <div style="font-size:7px; color:#8b9dc3; text-transform:uppercase; letter-spacing:0.5px">🌙 Noc</div>
                        <div style="font-size:14px; font-weight:800; color:#8b9dc3" id="v-load-night">—</div>
                        <div style="font-size:8px; color:#94a3b8">kWh</div>
                      </div>
                    </div>
                    <div style="display:flex; align-items:center; gap:4px; margin-top:2px">
                      <span style="font-size:8px; color:#f7b731">⚡ Z PV na dom:</span>
                      <span style="font-size:12px; font-weight:800; color:#2ecc71" id="v-load-from-pv">— kWh</span>
                      <span style="font-size:9px; color:#64748b" id="v-load-from-pv-pct"></span>
                    </div>
                  </div>
                  <!-- Sub-meters in Zużycie card -->
                  <div id="submeters-in-card" style="display:none; margin-top:6px; border-top:1px solid rgba(255,255,255,0.06); padding-top:6px"></div>
                </div>
              </div>

              <!-- 🔋 BATTERY (left, below PV) -->
              <div class="batt-area">
                <div class="node" id="batt-node" style="border-color: rgba(0,212,255,0.2); transition: border-color 0.5s, box-shadow 0.5s; display:flex; flex-direction: row; align-items: stretch; gap: 16px; padding: 16px;">
                  <!-- Left Graphic -->
                  <div class="battery-graphic" id="batt-graphic">
                    <div class="batt-bar"></div><div class="batt-bar"></div><div class="batt-bar"></div><div class="batt-bar"></div><div class="batt-bar"></div>
                    <div class="batt-bar"></div><div class="batt-bar"></div><div class="batt-bar"></div><div class="batt-bar"></div><div class="batt-bar"></div>
                  </div>
                  <!-- Right Details -->
                  <div style="flex: 1; display: flex; flex-direction: column; justify-content: center;">
                    <div class="node-title">🔋 Bateria</div>
                    <div style="display:flex; align-items:baseline; gap:8px; margin-top:4px;">
                      <div class="node-big" id="v-soc" style="color:#2ecc71">—%</div>
                      <div style="font-size:16px; font-weight:700; color:#fff" id="v-batt">— W</div>
                    </div>
                    <div class="node-dir" id="v-batt-dir" style="color:#00d4ff; margin-top:2px;">STANDBY</div>
                    <div id="v-batt-eta" style="font-size:10px; margin-top:3px; font-weight:600; transition:all 0.3s; display:none; animation: etaPulse 2.5s ease-in-out infinite"><span style="opacity:0.7">⏱</span> <span id="v-batt-eta-text">—</span></div>
                    <div class="node-detail" style="margin-top:auto; padding-top:8px;">
                      <div><span id="v-batt-v">— V</span> · <span id="v-batt-a">— A</span> · <span id="v-batt-temp">—°C</span></div>
                      <div style="display:flex; justify-content:space-between; margin-top:4px;">
                        <span id="v-batt-charge">↑ — kWh</span>
                        <span id="v-batt-discharge">↓ — kWh</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <!-- ⚡ INVERTER (center) -->
              <div class="inv-area">
                <div class="node inv-node" style="border-color: rgba(0,212,255,0.15); display:flex; flex-direction:column; align-items:center; padding:16px">
                  <div class="inv-box">
                    <img id="v-inv-img" src="https://smartinghome.pl/wp-content/uploads/2026/03/GoodWe-1.png" alt="Inverter" style="max-width:120px; max-height:90px; object-fit:contain" />
                    <div id="v-inv-icon" style="display:none; text-align:center">
                      <div class="inv-icon">⚡</div>
                      <div style="font-size:8px; color:#f39c12; line-height:1.2; margin-top:2px">Wgraj zdjęcie<br>w ⚙️ Ustawieniach</div>
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
              </div>

              <!-- 🔌 GRID / SIEĆ (right, below Home) -->
              <div class="grid-area">
                <div class="node" id="grid-node" style="border-color: rgba(231,76,60,0.15); transition: border-color 0.5s, box-shadow 0.5s">
                  <div style="display:flex; align-items:flex-end; gap:10px">
                    <div style="flex:1">
                      <div class="node-title">🔌 Sieć</div>
                      <div class="node-big" id="v-grid">— W</div>
                      <div class="node-dir" id="v-grid-dir" style="color:#e74c3c"></div>
                      <div class="node-detail" style="margin-top:6px">
                        <div><span id="v-grid-v1">— V</span> · <span id="v-grid-v2">— V</span> · <span id="v-grid-v3">— V</span></div>
                        <div id="v-grid-freq">— Hz</div>
                        <div style="margin-top:4px"><span style="color:#e74c3c">↓</span> <span id="v-grid-import">— kWh</span>&nbsp;<span style="color:#2ecc71">↑</span> <span id="v-grid-export">— kWh</span></div>
                      </div>
                    </div>
                    <div style="flex-shrink:0; max-width:55px">
                      <img id="v-grid-img" src="https://smartinghome.pl/wp-content/uploads/2026/03/slup-energetyka-1.png" alt="Sieć" style="width:100%; max-height:80px; object-fit:contain; opacity:0.85" />
                    </div>
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
          <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(120px, 1fr)); gap:8px; margin-top:10px">
            <div style="background:rgba(231,76,60,0.08); border-radius:10px; padding:10px; text-align:center">
              <div style="font-size:9px; color:#e74c3c; text-transform:uppercase; letter-spacing:0.5px">💸 Koszt importu</div>
              <div style="font-size:18px; font-weight:800; color:#e74c3c; margin-top:2px" id="ov-cost">— zł</div>
            </div>
            <div style="background:rgba(46,204,113,0.08); border-radius:10px; padding:10px; text-align:center">
              <div style="font-size:9px; color:#2ecc71; text-transform:uppercase; letter-spacing:0.5px">💵 Przychód</div>
              <div style="font-size:18px; font-weight:800; color:#2ecc71; margin-top:2px" id="ov-revenue">— zł</div>
            </div>
            <div style="background:rgba(0,212,255,0.08); border-radius:10px; padding:10px; text-align:center">
              <div style="font-size:9px; color:#00d4ff; text-transform:uppercase; letter-spacing:0.5px">🏦 Oszczędność</div>
              <div style="font-size:18px; font-weight:800; color:#00d4ff; margin-top:2px" id="ov-savings">— zł</div>
            </div>
            <div style="border-radius:10px; padding:10px; text-align:center; border:1px solid" id="ov-balance-card">
              <div style="font-size:9px; text-transform:uppercase; letter-spacing:0.5px">📊 Bilans dziś</div>
              <div style="font-size:20px; font-weight:900; margin-top:2px" id="ov-balance">— zł</div>
            </div>
          </div>

          <!-- 🌤️ AccuWeather Forecast -->
          <div class="card" style="margin-top:10px">
            <div class="card-title">🌤️ Prognoza pogody — AccuWeather</div>
            <div id="weather-forecast-strip" style="display:flex; gap:8px; overflow-x:auto; padding:4px 0">
              <div style="text-align:center; color:#64748b; padding:12px; font-size:11px; width:100%">Ładowanie prognozy...</div>
            </div>
          </div>

          <!-- 🌦️ Ecowitt Real-Time Weather -->
          <div class="card" style="margin-top:10px" id="ecowitt-weather-card">
            <div class="card-title">🌦️ Pogoda teraz — Ecowitt WH90</div>
            <div id="ecowitt-body" style="padding:4px 0">
              <div style="text-align:center; color:#64748b; font-size:11px">Włącz Ecowitt w ⚙️ Ustawieniach</div>
            </div>
          </div>

          <!-- 📊 HEMS Score -->
          <div class="card" style="margin-top:10px">
            <div class="card-title">📊 Sprawność HEMS</div>
            <div id="hems-score-display" style="padding:4px 0">
              <div style="text-align:center; color:#64748b; font-size:11px">Obliczanie...</div>
            </div>
          </div>

          <!-- HEMS Recommendation -->
          <div class="card" style="margin-top:10px">
            <div class="card-title">💡 Rekomendacja HEMS</div>
            <div class="recommendation" id="v-hems-rec">Ładowanie danych...</div>
          </div>

          <!-- 📊 Podliczniki energii -->
          <div class="submeter-section" id="submeters-section" style="display:none">
            <div class="submeter-header">
              <div class="submeter-header-title">📊 Podliczniki energii</div>
              <button style="padding:4px 10px; border:1px solid rgba(255,255,255,0.08); background:transparent; color:#94a3b8; font-size:10px; cursor:pointer; border-radius:6px; transition:all 0.2s" onclick="this.getRootNode().host._switchTab('settings')" title="Konfiguruj podliczniki">⚙️ Konfiguruj</button>
            </div>
            <div class="submeter-grid" id="submeters-grid">
              <div class="submeter-empty">Dodaj podliczniki w ⚙️ Ustawieniach</div>
            </div>
          </div>
        </div>

        <!-- ═══════ TAB: ENERGY ═══════ -->
        <div class="tab-content" data-tab="energy">

          <!-- ROW 1: Daily Energy Balance KPIs -->
          <div class="g4" style="margin-bottom:14px">
            <div class="card" style="text-align:center; padding:14px 8px">
              <div style="font-size:9px; color:#f7b731; text-transform:uppercase; letter-spacing:1px">☀️ Produkcja PV dziś</div>
              <div style="font-size:28px; font-weight:800; color:#f7b731; margin:4px 0" id="v-en-pv-today">—</div>
              <div style="font-size:10px; color:#94a3b8">kWh</div>
            </div>
            <div class="card" style="text-align:center; padding:14px 8px">
              <div style="font-size:9px; color:#e74c3c; text-transform:uppercase; letter-spacing:1px">↓ Import z sieci</div>
              <div style="font-size:28px; font-weight:800; color:#e74c3c; margin:4px 0" id="v-en-import-today">—</div>
              <div style="font-size:10px; color:#94a3b8">kWh</div>
            </div>
            <div class="card" style="text-align:center; padding:14px 8px">
              <div style="font-size:9px; color:#2ecc71; text-transform:uppercase; letter-spacing:1px">↑ Eksport do sieci</div>
              <div style="font-size:28px; font-weight:800; color:#2ecc71; margin:4px 0" id="v-en-export-today">—</div>
              <div style="font-size:10px; color:#94a3b8">kWh</div>
            </div>
            <div class="card" style="text-align:center; padding:14px 8px">
              <div style="font-size:9px; color:#00d4ff; text-transform:uppercase; letter-spacing:1px">📊 Saldo netto</div>
              <div style="font-size:28px; font-weight:800; margin:4px 0" id="v-en-net-today">—</div>
              <div style="font-size:10px; color:#94a3b8">kWh</div>
            </div>
          </div>

          <!-- ROW 1b: Day/Night Consumption Breakdown -->
          <div class="card" style="margin-bottom:14px">
            <div class="card-title">🏠 Zużycie — podział dzień / noc</div>
            <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; margin-bottom:12px">
              <div style="background:rgba(247,183,49,0.08); border-radius:12px; padding:14px 8px; text-align:center">
                <div style="font-size:9px; color:#f7b731; text-transform:uppercase; letter-spacing:1px">☀️ Dzień (PV)</div>
                <div style="font-size:26px; font-weight:800; color:#f7b731; margin:4px 0" id="v-en-load-day">—</div>
                <div style="font-size:10px; color:#94a3b8">kWh</div>
              </div>
              <div style="background:rgba(139,157,195,0.08); border-radius:12px; padding:14px 8px; text-align:center">
                <div style="font-size:9px; color:#8b9dc3; text-transform:uppercase; letter-spacing:1px">🌙 Noc</div>
                <div style="font-size:26px; font-weight:800; color:#8b9dc3; margin:4px 0" id="v-en-load-night">—</div>
                <div style="font-size:10px; color:#94a3b8">kWh</div>
              </div>
              <div style="background:rgba(46,204,113,0.08); border-radius:12px; padding:14px 8px; text-align:center">
                <div style="font-size:9px; color:#2ecc71; text-transform:uppercase; letter-spacing:1px">⚡ Z PV na dom</div>
                <div style="font-size:26px; font-weight:800; color:#2ecc71; margin:4px 0" id="v-en-load-from-pv">—</div>
                <div style="font-size:10px; color:#94a3b8">kWh</div>
              </div>
            </div>
            <!-- Stacked bar: day/night ratio -->
            <div style="display:flex; align-items:center; gap:8px; margin-bottom:6px">
              <span style="font-size:10px; color:#94a3b8; min-width:80px">Dzień vs Noc</span>
              <div style="flex:1; height:14px; border-radius:7px; overflow:hidden; background:rgba(255,255,255,0.06); display:flex">
                <div id="v-en-daybar" style="height:100%; background:linear-gradient(90deg,#f7b731,#f39c12); transition:width 0.6s ease; width:50%"></div>
                <div id="v-en-nightbar" style="height:100%; background:linear-gradient(90deg,#64748b,#8b9dc3); transition:width 0.6s ease; width:50%"></div>
              </div>
              <span style="font-size:10px; color:#f7b731; font-weight:700; min-width:35px" id="v-en-day-pct">—%</span>
            </div>
            <!-- PV self-consumption bar -->
            <div style="display:flex; align-items:center; gap:8px">
              <span style="font-size:10px; color:#94a3b8; min-width:80px">PV → Dom</span>
              <div style="flex:1; height:14px; border-radius:7px; overflow:hidden; background:rgba(255,255,255,0.06)">
                <div id="v-en-pv-home-bar" style="height:100%; background:linear-gradient(90deg,#2ecc71,#f7b731); transition:width 0.6s ease; width:0%"></div>
              </div>
              <span style="font-size:10px; color:#2ecc71; font-weight:700; min-width:35px" id="v-en-pv-home-pct">—%</span>
            </div>
          </div>

          <!-- ROW 2: Real-time Power KPIs -->
          <div class="g4" style="margin-bottom:14px">
            <div class="card" style="text-align:center; padding:12px 8px; border-left:3px solid #f7b731">
              <div style="font-size:9px; color:#f7b731; text-transform:uppercase">☀️ Produkcja PV</div>
              <div style="font-size:22px; font-weight:800; color:#f7b731; margin-top:4px" id="v-en-pv-power">— W</div>
            </div>
            <div class="card" style="text-align:center; padding:12px 8px; border-left:3px solid #2ecc71">
              <div style="font-size:9px; color:#2ecc71; text-transform:uppercase">🏠 Zużycie</div>
              <div style="font-size:22px; font-weight:800; color:#2ecc71; margin-top:4px" id="v-en-load-power">— W</div>
            </div>
            <div class="card" style="text-align:center; padding:12px 8px; border-left:3px solid #a855f7">
              <div style="font-size:9px; color:#a855f7; text-transform:uppercase">⚡ Nadwyżka PV</div>
              <div style="font-size:22px; font-weight:800; color:#a855f7; margin-top:4px" id="v-en-surplus">— W</div>
            </div>
            <div class="card" style="text-align:center; padding:12px 8px; border-left:3px solid #00d4ff">
              <div style="font-size:9px; color:#00d4ff; text-transform:uppercase">🔋 Bateria</div>
              <div style="font-size:22px; font-weight:800; color:#00d4ff; margin-top:4px" id="v-en-batt-power">— W</div>
              <div style="font-size:11px; color:#94a3b8; margin-top:2px" id="v-en-batt-info">—% · STANDBY</div>
            </div>
          </div>

          <!-- ROW 3: Grid 3F Extended + PV Strings -->
          <div class="grid-cards gc-2" style="margin-bottom:14px">
            <div class="card">
              <div class="card-title">⚡ Sieć 3F — parametry</div>
              <div style="margin-bottom:8px">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase; margin-bottom:6px">Napięcie</div>
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
              </div>
              <div style="display:grid; grid-template-columns:1fr 1fr; gap:0 16px; margin-top:6px; padding-top:8px; border-top:1px solid rgba(255,255,255,0.06)">
                <div>
                  <div style="font-size:9px; color:#64748b; text-transform:uppercase; margin-bottom:4px">Prąd (A)</div>
                  <div class="dr"><span class="lb">L1</span><span class="vl" id="v-en-a1">— A</span></div>
                  <div class="dr"><span class="lb">L2</span><span class="vl" id="v-en-a2">— A</span></div>
                  <div class="dr"><span class="lb">L3</span><span class="vl" id="v-en-a3">— A</span></div>
                </div>
                <div>
                  <div style="font-size:9px; color:#64748b; text-transform:uppercase; margin-bottom:4px">Moc (W)</div>
                  <div class="dr"><span class="lb">L1</span><span class="vl" id="v-en-p1">— W</span></div>
                  <div class="dr"><span class="lb">L2</span><span class="vl" id="v-en-p2">— W</span></div>
                  <div class="dr"><span class="lb">L3</span><span class="vl" id="v-en-p3">— W</span></div>
                </div>
              </div>
              <div class="dr" style="margin-top:8px; padding-top:6px; border-top:1px solid rgba(255,255,255,0.06)"><span class="lb">Częstotliwość</span><span class="vl" id="v-e-freq">— Hz</span></div>
            </div>
            <div class="card">
              <div class="card-title">☀️ Stringi PV — szczegóły</div>
              <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px">
                <div style="background:rgba(247,183,49,0.06); border-radius:10px; padding:12px; cursor:pointer" onclick="this.getRootNode().host._editPvLabel(1)" title="Kliknij aby zmienić nazwę">
                  <div style="font-size:11px; font-weight:700; color:#f7b731; margin-bottom:6px; display:flex; align-items:center; gap:4px"><span id="v-en-pv1-label">PV1</span><span class="pv-config-btn" onclick="event.stopPropagation(); this.getRootNode().host._openPvStringConfig(1)" title="Konfiguracja stringa">⚙️</span></div>
                  <div style="font-size:22px; font-weight:800; color:#fff" id="v-en-pv1-p">— W</div>
                  <div style="font-size:11px; color:#94a3b8; margin-top:4px"><span id="v-en-pv1-v">— V</span> · <span id="v-en-pv1-a">— A</span></div>
                </div>
                <div style="background:rgba(247,183,49,0.06); border-radius:10px; padding:12px; cursor:pointer" onclick="this.getRootNode().host._editPvLabel(2)" title="Kliknij aby zmienić nazwę">
                  <div style="font-size:11px; font-weight:700; color:#f7b731; margin-bottom:6px; display:flex; align-items:center; gap:4px"><span id="v-en-pv2-label">PV2</span><span class="pv-config-btn" onclick="event.stopPropagation(); this.getRootNode().host._openPvStringConfig(2)" title="Konfiguracja stringa">⚙️</span></div>
                  <div style="font-size:22px; font-weight:800; color:#fff" id="v-en-pv2-p">— W</div>
                  <div style="font-size:11px; color:#94a3b8; margin-top:4px"><span id="v-en-pv2-v">— V</span> · <span id="v-en-pv2-a">— A</span></div>
                </div>
                <div style="background:rgba(247,183,49,0.06); border-radius:10px; padding:12px; display:none" id="v-en-pv3-box">
                  <div style="font-size:11px; font-weight:700; color:#f7b731; margin-bottom:6px">PV3</div>
                  <div style="font-size:22px; font-weight:800; color:#fff" id="v-en-pv3-p">— W</div>
                </div>
                <div style="background:rgba(247,183,49,0.06); border-radius:10px; padding:12px; display:none" id="v-en-pv4-box">
                  <div style="font-size:11px; font-weight:700; color:#f7b731; margin-bottom:6px">PV4</div>
                  <div style="font-size:22px; font-weight:800; color:#fff" id="v-en-pv4-p">— W</div>
                </div>
              </div>
              <div style="margin-top:10px; padding-top:8px; border-top:1px solid rgba(255,255,255,0.06)">
                <div class="dr"><span class="lb">☀️ Łączna moc PV</span><span class="vl" style="color:#f7b731; font-weight:800" id="v-en-pv-total">— W</span></div>
                <div class="dr"><span class="lb">📊 Produkcja dziś</span><span class="vl" id="v-en-pv-today2">— kWh</span></div>
              </div>
            </div>
          </div>

          <!-- ROW 4: Autarky & Self-consumption + Inverter -->
          <div class="grid-cards gc-2" style="margin-bottom:14px">
            <div class="card">
              <div class="card-title">🛡️ Autarkia & Autokonsumpcja</div>
              <div style="margin-bottom:12px">
                <div style="display:flex; justify-content:space-between; font-size:11px; margin-bottom:4px">
                  <span style="color:#94a3b8">Autarkia dziś</span>
                  <span style="color:#2ecc71; font-weight:800" id="v-en-autarky">—%</span>
                </div>
                <div style="background:rgba(255,255,255,0.06); border-radius:6px; height:10px; overflow:hidden">
                  <div style="height:100%; border-radius:6px; background:linear-gradient(90deg,#2ecc71,#27ae60); transition:width 0.5s; width:0%" id="v-en-autarky-bar"></div>
                </div>
                <div style="font-size:9px; color:#64748b; margin-top:2px">ile energii pochodzi z własnej instalacji PV</div>
              </div>
              <div style="margin-bottom:12px">
                <div style="display:flex; justify-content:space-between; font-size:11px; margin-bottom:4px">
                  <span style="color:#94a3b8">Autokonsumpcja dziś</span>
                  <span style="color:#00d4ff; font-weight:800" id="v-en-selfcons">—%</span>
                </div>
                <div style="background:rgba(255,255,255,0.06); border-radius:6px; height:10px; overflow:hidden">
                  <div style="height:100%; border-radius:6px; background:linear-gradient(90deg,#00d4ff,#0891b2); transition:width 0.5s; width:0%" id="v-en-selfcons-bar"></div>
                </div>
                <div style="font-size:9px; color:#64748b; margin-top:2px">ile wyprodukowanej energii zużywamy na miejscu</div>
              </div>
              <div style="padding-top:8px; border-top:1px solid rgba(255,255,255,0.06)">
                <div class="dr"><span class="lb">🏠 Z PV na dom dziś</span><span class="vl" style="color:#f7b731" id="v-en-home-from-pv">— kWh</span></div>
                <div class="dr"><span class="lb">📊 Saldo sieci dziś</span><span class="vl" id="v-en-net-grid">— kWh</span></div>
                <div class="dr"><span class="lb">🎯 Celność prognoz PV</span><span class="vl" id="v-en-forecast-accuracy">—%</span></div>
              </div>
            </div>
            <div class="card">
              <div class="card-title">⚙️ Falownik & Bateria</div>
              <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:10px">
                <div style="background:rgba(247,183,49,0.08); border-radius:10px; padding:10px; text-align:center">
                  <div style="font-size:9px; color:#f7b731; text-transform:uppercase">Moc falownika</div>
                  <div style="font-size:22px; font-weight:800; color:#fff; margin-top:2px" id="v-en-inv-p">— W</div>
                </div>
                <div style="background:rgba(231,76,60,0.08); border-radius:10px; padding:10px; text-align:center">
                  <div style="font-size:9px; color:#e74c3c; text-transform:uppercase">Temp. falownika</div>
                  <div style="font-size:22px; font-weight:800; color:#fff; margin-top:2px" id="v-en-inv-t">— °C</div>
                </div>
              </div>
              <div style="padding-top:8px; border-top:1px solid rgba(255,255,255,0.06)">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase; margin-bottom:6px">🔋 Bateria</div>
                <div class="dr"><span class="lb">Napięcie</span><span class="vl" id="v-en-batt-v">— V</span></div>
                <div class="dr"><span class="lb">Prąd</span><span class="vl" id="v-en-batt-a">— A</span></div>
                <div class="dr"><span class="lb">Temperatura</span><span class="vl" id="v-en-batt-temp">— °C</span></div>
                <div class="dr"><span class="lb">Energia dostępna</span><span class="vl" style="color:#00d4ff" id="v-en-batt-energy">— kWh</span></div>
                <div class="dr"><span class="lb">Czas pracy (est.)</span><span class="vl" id="v-en-batt-runtime">—</span></div>
              </div>
              <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:10px; padding-top:8px; border-top:1px solid rgba(255,255,255,0.06)">
                <div style="background:rgba(46,204,113,0.08); border-radius:8px; padding:8px; text-align:center">
                  <div style="font-size:9px; color:#2ecc71; text-transform:uppercase">↑ Ładowanie dziś</div>
                  <div style="font-size:16px; font-weight:800; color:#2ecc71; margin-top:2px" id="v-en-batt-charge">— kWh</div>
                </div>
                <div style="background:rgba(243,156,18,0.08); border-radius:8px; padding:8px; text-align:center">
                  <div style="font-size:9px; color:#f39c12; text-transform:uppercase">↓ Rozładowanie dziś</div>
                  <div style="font-size:16px; font-weight:800; color:#f39c12; margin-top:2px" id="v-en-batt-discharge">— kWh</div>
                </div>
              </div>
            </div>
          </div>

          <!-- ROW 5: PV Forecast Extended -->
          <div class="card" style="margin-bottom:14px">
            <div class="card-title">☀️ Prognoza PV & Pogoda</div>
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(130px, 1fr)); gap:8px; margin-bottom:10px">
              <div style="background:rgba(247,183,49,0.08); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#f7b731; text-transform:uppercase">PV teraz</div>
                <div style="font-size:20px; font-weight:800; color:#f7b731; margin-top:2px" id="v-en-forecast-now">— W</div>
              </div>
              <div style="background:rgba(247,183,49,0.08); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#f7b731; text-transform:uppercase">Prognoza dziś</div>
                <div style="font-size:20px; font-weight:800; color:#fff; margin-top:2px" id="v-forecast-today-tab">— kWh</div>
              </div>
              <div style="background:rgba(0,212,255,0.08); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#00d4ff; text-transform:uppercase">Pozostało dziś</div>
                <div style="font-size:20px; font-weight:800; color:#00d4ff; margin-top:2px" id="v-en-forecast-remaining">— kWh</div>
              </div>
              <div style="background:rgba(168,85,247,0.08); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#a855f7; text-transform:uppercase">Prognoza jutro</div>
                <div style="font-size:20px; font-weight:800; color:#a855f7; margin-top:2px" id="v-forecast-tomorrow-tab">— kWh</div>
              </div>
            </div>
            <div style="padding-top:8px; border-top:1px solid rgba(255,255,255,0.06)">
              <div class="dr"><span class="lb">🌡️ Temperatura</span><span class="vl" id="v-energy-temp">—</span></div>
              <div class="dr"><span class="lb">🌡️ RealFeel</span><span class="vl" id="v-energy-realfeel">—</span></div>
              <div class="dr"><span class="lb">☁️ Zachmurzenie</span><span class="vl" id="v-energy-clouds">—</span></div>
              <div class="dr"><span class="lb">🌤️ Warunki</span><span class="vl" id="v-energy-condition">—</span></div>
              <div class="dr"><span class="lb">☀️ Godziny słon. dziś</span><span class="vl" id="v-energy-sunhours">—</span></div>
              <div class="dr"><span class="lb">🔆 UV Index</span><span class="vl" id="v-energy-uv">—</span></div>
              <div class="dr"><span class="lb">💨 Wiatr</span><span class="vl" id="v-energy-wind">—</span></div>
            </div>
          </div>

          <!-- ROW 6: Ecowitt Local Weather -->
          <div class="card" style="margin-bottom:14px; display:none" id="en-ecowitt-card">
            <div class="card-title">🌦️ Stacja lokalna — Ecowitt</div>
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:8px">
              <div style="background:rgba(231,76,60,0.06); border-radius:8px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#e74c3c; text-transform:uppercase">🌡️ Temperatura</div>
                <div style="font-size:18px; font-weight:800; color:#fff; margin-top:2px" id="v-en-eco-temp">—°C</div>
              </div>
              <div style="background:rgba(0,212,255,0.06); border-radius:8px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#00d4ff; text-transform:uppercase">💧 Wilgotność</div>
                <div style="font-size:18px; font-weight:800; color:#fff; margin-top:2px" id="v-en-eco-hum">—%</div>
              </div>
              <div style="background:rgba(247,183,49,0.06); border-radius:8px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#f7b731; text-transform:uppercase">☀️ Nasłoneczn.</div>
                <div style="font-size:18px; font-weight:800; color:#fff; margin-top:2px" id="v-en-eco-solar">— W/m²</div>
              </div>
              <div style="background:rgba(46,204,113,0.06); border-radius:8px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#2ecc71; text-transform:uppercase">💨 Wiatr</div>
                <div style="font-size:18px; font-weight:800; color:#fff; margin-top:2px" id="v-en-eco-wind">—</div>
              </div>
              <div style="background:rgba(0,150,255,0.06); border-radius:8px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#0096ff; text-transform:uppercase">🌧️ Deszcz</div>
                <div style="font-size:18px; font-weight:800; color:#fff; margin-top:2px" id="v-en-eco-rain">— mm/h</div>
              </div>
              <div style="background:rgba(168,85,247,0.06); border-radius:8px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#a855f7; text-transform:uppercase">📊 Ciśnienie</div>
                <div style="font-size:18px; font-weight:800; color:#fff; margin-top:2px" id="v-en-eco-pressure">— hPa</div>
              </div>
            </div>
          </div>

          <!-- ROW 7: Quick Actions -->
          <div class="card">
            <div class="card-title">⚡ Szybkie akcje</div>

            <!-- START row -->
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; padding:8px 0">

              <!-- ═══ CHARGE button ═══ -->
              <div style="background:rgba(46,204,113,0.06); border:1px solid rgba(46,204,113,0.15); border-radius:10px; padding:14px; text-align:center">
                <div style="font-size:28px; margin-bottom:6px">🔋</div>
                <div style="font-size:13px; font-weight:700; color:#2ecc71; margin-bottom:4px">Wymuś Ładowanie</div>
                <div style="font-size:9px; color:#64748b; line-height:1.4; margin-bottom:10px">
                  eco_charge · soc=100% · power=100%<br>current=18.5A
                </div>
                <button id="btn-force-charge" class="action-btn" style="width:100%; padding:10px; background:rgba(46,204,113,0.15); border:1px solid #2ecc71; color:#2ecc71; font-weight:700; font-size:13px; border-radius:8px; cursor:pointer; transition:transform 0.1s ease, box-shadow 0.2s ease" onclick="this.getRootNode().host._executeForceAction('charge')">▶ ŁADUJ</button>
                <div id="fc-charge-status" style="font-size:10px; color:#64748b; text-align:center; min-height:16px; margin-top:6px">— oczekuje</div>
              </div>

              <!-- ═══ DISCHARGE button ═══ -->
              <div style="background:rgba(231,76,60,0.06); border:1px solid rgba(231,76,60,0.15); border-radius:10px; padding:14px; text-align:center">
                <div style="font-size:28px; margin-bottom:6px">⚡</div>
                <div style="font-size:13px; font-weight:700; color:#e74c3c; margin-bottom:4px">Wymuś Rozładowanie</div>
                <div style="font-size:9px; color:#64748b; line-height:1.4; margin-bottom:10px">
                  eco_discharge · soc=5% · power=100%<br>export=16kW · grid_export=ON
                </div>
                <button id="btn-force-discharge" class="action-btn" style="width:100%; padding:10px; background:rgba(231,76,60,0.15); border:1px solid #e74c3c; color:#e74c3c; font-weight:700; font-size:13px; border-radius:8px; cursor:pointer; transition:transform 0.1s ease, box-shadow 0.2s ease" onclick="this.getRootNode().host._executeForceAction('discharge')">▶ ROZŁADUJ</button>
                <div id="fc-discharge-status" style="font-size:10px; color:#64748b; text-align:center; min-height:16px; margin-top:6px">— oczekuje</div>
              </div>

            </div>

            <!-- STOP row -->
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; padding:4px 0">

              <!-- ═══ STOP CHARGE ═══ -->
              <div style="text-align:center">
                <button id="btn-stop-charge" class="action-btn" style="width:100%; padding:8px; background:rgba(245,158,11,0.12); border:1px solid rgba(245,158,11,0.4); color:#f59e0b; font-weight:700; font-size:12px; border-radius:8px; cursor:pointer; transition:transform 0.1s ease, box-shadow 0.2s ease" onclick="this.getRootNode().host._executeForceAction('stop_charge')">⏹ STOP Ładow.</button>
                <div id="fc-stop_charge-status" style="font-size:9px; color:#64748b; text-align:center; min-height:14px; margin-top:4px"></div>
              </div>

              <!-- ═══ STOP DISCHARGE ═══ -->
              <div style="text-align:center">
                <button id="btn-stop-discharge" class="action-btn" style="width:100%; padding:8px; background:rgba(245,158,11,0.12); border:1px solid rgba(245,158,11,0.4); color:#f59e0b; font-weight:700; font-size:12px; border-radius:8px; cursor:pointer; transition:transform 0.1s ease, box-shadow 0.2s ease" onclick="this.getRootNode().host._executeForceAction('stop_discharge')">⏹ STOP Rozład.</button>
                <div id="fc-stop_discharge-status" style="font-size:9px; color:#64748b; text-align:center; min-height:14px; margin-top:4px"></div>
              </div>

            </div>

            <!-- EMERGENCY STOP -->
            <div style="padding:4px 0 8px 0">
              <button id="btn-emergency-stop" class="action-btn" style="width:100%; padding:12px; background:rgba(231,76,60,0.2); border:2px solid #e74c3c; color:#e74c3c; font-weight:900; font-size:14px; border-radius:10px; cursor:pointer; transition:transform 0.1s ease, box-shadow 0.2s ease; letter-spacing:1px" onclick="this.getRootNode().host._executeForceAction('emergency_stop')">🚨 EMERGENCY STOP</button>
              <div id="fc-emergency_stop-status" style="font-size:9px; color:#64748b; text-align:center; min-height:14px; margin-top:4px"></div>
            </div>

          </div>

        </div>

        <!-- ═══════ TAB: TARIFF & RCE ═══════ -->
        <div class="tab-content" data-tab="tariff">

          <!-- ROW 1: RCE Price Cards -->
                    <div class="g4" style="margin-bottom:14px">
            <div class="card" style="text-align:center; padding:14px 8px">
              <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:1px">RCE teraz</div>
              <div style="font-size:28px; font-weight:800; margin:4px 0" id="v-rce-now">—</div>
              <div style="font-size:10px; color:#94a3b8" id="v-rce-now-mwh">— PLN/MWh</div>
            </div>
            <div class="card" style="text-align:center; padding:14px 8px">
              <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:1px">RCE +1h</div>
              <div style="font-size:22px; font-weight:700; margin:4px 0; color:#a0aec0" id="v-rce-1h">—</div>
              <div style="font-size:10px; color:#94a3b8">zł/kWh</div>
            </div>
            <div class="card" style="text-align:center; padding:14px 8px">
              <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:1px">RCE +2h</div>
              <div style="font-size:22px; font-weight:700; margin:4px 0; color:#a0aec0" id="v-rce-2h">—</div>
              <div style="font-size:10px; color:#94a3b8">zł/kWh</div>
            </div>
            <div class="card" style="text-align:center; padding:14px 8px">
              <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:1px">RCE +3h</div>
              <div style="font-size:22px; font-weight:700; margin:4px 0; color:#a0aec0" id="v-rce-3h">—</div>
              <div style="font-size:10px; color:#94a3b8">zł/kWh</div>
            </div>
          </div>

          <!-- ROW 2: RCE Stats + Trend -->
                    <div class="g4" style="margin-bottom:14px">
            <div class="card" style="text-align:center; padding:12px 8px">
              <div style="font-size:9px; color:#64748b; text-transform:uppercase">Średnia dziś</div>
              <div style="font-size:18px; font-weight:700; color:#00d4ff; margin-top:4px" id="v-rce-avg2">—</div>
              <div style="font-size:10px; color:#94a3b8">zł/kWh</div>
            </div>
            <div class="card" style="text-align:center; padding:12px 8px">
              <div style="font-size:9px; color:#64748b; text-transform:uppercase">Min / Max</div>
              <div style="font-size:14px; font-weight:700; margin-top:6px"><span style="color:#2ecc71" id="v-rce-min">—</span> <span style="color:#64748b">/</span> <span style="color:#e74c3c" id="v-rce-max">—</span></div>
              <div style="font-size:10px; color:#94a3b8">zł/kWh</div>
            </div>
            <div class="card" style="text-align:center; padding:12px 8px">
              <div style="font-size:9px; color:#64748b; text-transform:uppercase">vs Średnia</div>
              <div style="font-size:18px; font-weight:700; margin-top:4px" id="v-rce-vs-avg">—%</div>
              <div style="font-size:10px; color:#94a3b8" id="v-rce-vs-label">—</div>
            </div>
            <div class="card" style="text-align:center; padding:12px 8px">
              <div style="font-size:9px; color:#64748b; text-transform:uppercase">Trend</div>
              <div style="font-size:18px; font-weight:700; margin-top:4px" id="v-rce-trend2">—</div>
              <div style="font-size:10px; color:#94a3b8" id="v-rce-trend-label">—</div>
            </div>
          </div>

          <!-- ROW 3: Time Windows + G13 Zone -->
          <div class="g3" style="margin-bottom:14px">
            <div class="card" style="padding:14px; border-left:3px solid #2ecc71">
              <div style="display:flex; align-items:center; gap:6px; margin-bottom:8px">
                <div style="width:10px; height:10px; border-radius:50%; background:#2ecc71"></div>
                <div style="font-size:10px; color:#2ecc71; text-transform:uppercase; font-weight:700">Najtaniej dziś</div>
              </div>
              <div style="font-size:18px; font-weight:800; color:#fff" id="v-cheapest-window">—</div>
            </div>
            <div class="card" style="padding:14px; border-left:3px solid #e74c3c">
              <div style="display:flex; align-items:center; gap:6px; margin-bottom:8px">
                <div style="width:10px; height:10px; border-radius:50%; background:#e74c3c"></div>
                <div style="font-size:10px; color:#e74c3c; text-transform:uppercase; font-weight:700">Najdrożej dziś</div>
              </div>
              <div style="font-size:18px; font-weight:800; color:#fff" id="v-expensive-window">—</div>
            </div>
            <div class="card" style="padding:14px; border-left:3px solid #00d4ff">
              <div style="display:flex; align-items:center; gap:6px; margin-bottom:8px">
                <div style="width:10px; height:10px; border-radius:50%; background:#00d4ff"></div>
                <div style="font-size:10px; color:#00d4ff; text-transform:uppercase; font-weight:700">Kompas PSE</div>
              </div>
              <div style="font-size:16px; font-weight:700; color:#fff" id="v-kompas">—</div>
            </div>
          </div>

          <!-- ROW 4: G13 + RCE Details -->
          <div class="grid-cards gc-2" style="margin-bottom:14px">
            <div class="card">
              <div class="card-title" id="tariff-card-title">⏰ Taryfa G13 <span id="g13-weekend-badge" style="font-size:9px; background:#2ecc71; color:#000; padding:2px 6px; border-radius:4px; font-weight:700; margin-left:6px; display:none">WEEKEND</span></div>
              <div class="dr"><span class="lb">Trwająca strefa</span><span id="v-g13-zone-badge" class="status-badge">—</span></div>
              <div class="dr"><span class="lb">Sezon</span><span class="vl" id="v-g13-season">—</span></div>
              <div class="dr"><span class="lb">Cena zakupu</span><span class="vl" id="v-g13-price-tab" style="font-weight:800">— zł/kWh</span></div>
              <div class="dr"><span class="lb">Cena sprzedaży RCE</span><span class="vl" id="v-rce-sell" style="color:#2ecc71">— zł/kWh</span></div>
              <div class="dr"><span class="lb">Spread G13↔RCE</span><span class="vl" id="v-spread" style="font-weight:700">— zł</span></div>
              <!-- Dynamic G13 Timeline -->
              <div style="margin-top:10px; padding:8px; border-radius:6px; background:rgba(255,255,255,0.03)">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase; margin-bottom:6px" id="g13-timeline-label">Harmonogram G13 <span id="g13-day-type" style="color:#f7b731">(dzień roboczy)</span></div>
                <div style="position:relative">
                  <div style="display:flex; gap:1px; height:22px; border-radius:4px; overflow:hidden" id="g13-timeline"></div>
                  <div id="g13-now-marker" style="position:absolute; top:-4px; width:2px; height:30px; background:#fff; border-radius:1px; z-index:10; transition:left 0.3s"></div>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:8px; color:#64748b; margin-top:2px"><span>0</span><span>3</span><span>6</span><span>9</span><span>12</span><span>15</span><span>18</span><span>21</span><span>24</span></div>
                <div style="display:flex; gap:8px; margin-top:6px; font-size:9px; flex-wrap:wrap">
                  <span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:#2ecc71;vertical-align:middle"></span> Off-peak (najtaniej)</span>
                  <span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:#e67e22;vertical-align:middle"></span> Przedpołudniowa</span>
                  <span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:#e74c3c;vertical-align:middle"></span> Popołudniowa (najdrożej)</span>
                </div>
              </div>
            </div>
            <div class="card">
              <div class="card-title">📈 Rynek RCE PSE</div>
              <div class="dr"><span class="lb">Ocena RCE</span><span class="vl" id="v-rce-grade" style="font-weight:800">—</span></div>
              <div class="dr"><span class="lb">Mediana dziś</span><span class="vl" id="v-rce-median">— zł</span></div>
              <div class="dr"><span class="lb">Średnia jutro</span><span class="vl" id="v-rce-avg-tomorrow">— zł</span></div>
              <div class="dr"><span class="lb">Jutro vs dziś</span><span class="vl" id="v-rce-tomorrow-vs">—%</span></div>
              <div class="dr"><span class="lb">Najtaniej jutro</span><span class="vl" id="v-cheapest-tomorrow">—</span></div>
              <div class="dr"><span class="lb">Najdrożej jutro</span><span class="vl" id="v-expensive-tomorrow">—</span></div>
              <!-- Net-billing strategy -->
              <div style="margin-top:10px; padding:8px; border-radius:6px; background:rgba(255,255,255,0.03)">
                <div style="font-size:9px; color:#f7b731; text-transform:uppercase; font-weight:700; margin-bottom:4px">💰 Strategia RCE Net-Billing</div>
                <div style="font-size:10px; color:#cbd5e1; line-height:1.5">
                  <div>🌞 <strong style="color:#e74c3c">10–16h</strong> = ❌ NIE sprzedawaj (0.10–0.30 zł)</div>
                  <div>🌇 <strong style="color:#2ecc71">17–22h</strong> = 💰 SPRZEDAWAJ (0.60–1.20 zł)</div>
                  <div>🌙 <strong style="color:#00d4ff">22–06h</strong> = ⚡ ładuj baterię (G13 off-peak)</div>
                  <div>🌅 <strong style="color:#f7b731">06–09h</strong> = 📈 rosnące ceny (0.50–0.90 zł)</div>
                  <div style="margin-top:4px; color:#94a3b8; font-size:9px">Sprzedaż: RCE × 1.23 (współcz. prosumencki 2026)</div>
                </div>
              </div>
            </div>
          </div>

          <!-- ROW 5: HEMS Recommendation -->
          <div class="card" style="margin-bottom:14px; border-left:3px solid #f7b731; padding:14px">
            <div style="display:flex; align-items:center; gap:8px">
              <div style="font-size:20px">⚡</div>
              <div>
                <div style="font-size:10px; color:#f7b731; text-transform:uppercase; font-weight:700">Rekomendacja HEMS</div>
                <div style="font-size:14px; font-weight:600; color:#fff; margin-top:2px" id="v-hems-rec-tariff">—</div>
              </div>
            </div>
          </div>

          <!-- ROW 6: Economics -->
                    <div class="g4" style="margin-bottom:14px">
            <div class="card" style="text-align:center; padding:12px 8px">
              <div style="font-size:9px; color:#64748b; text-transform:uppercase">Oszczędności</div>
              <div style="font-size:20px; font-weight:800; color:#2ecc71; margin-top:6px" id="v-savings">—</div>
              <div style="font-size:10px; color:#94a3b8">zł dziś</div>
            </div>
            <div class="card" style="text-align:center; padding:12px 8px">
              <div style="font-size:9px; color:#64748b; text-transform:uppercase">Przychód eksport</div>
              <div style="font-size:20px; font-weight:800; color:#00d4ff; margin-top:6px" id="v-export-rev">—</div>
              <div style="font-size:10px; color:#94a3b8">zł dziś</div>
            </div>
            <div class="card" style="text-align:center; padding:12px 8px">
              <div style="font-size:9px; color:#64748b; text-transform:uppercase">Koszt importu</div>
              <div style="font-size:20px; font-weight:800; color:#e74c3c; margin-top:6px" id="v-import-cost">—</div>
              <div style="font-size:10px; color:#94a3b8">zł dziś</div>
            </div>
            <div class="card" style="text-align:center; padding:12px 8px">
              <div style="font-size:9px; color:#64748b; text-transform:uppercase">Bilans netto</div>
              <div style="font-size:20px; font-weight:800; margin-top:6px" id="v-net-balance">—</div>
              <div style="font-size:10px; color:#94a3b8">zł dziś</div>
            </div>
          </div>

          <!-- ROW 7: Tauron 2026 Pricing Table -->
          <div class="card" style="margin-bottom:14px">
            <div class="card-title">📋 Cennik Tauron 2026 — porównanie taryf</div>
            <div style="overflow-x:auto">
              <table style="width:100%; border-collapse:collapse; font-size:10px; color:#cbd5e1">
                <thead>
                  <tr style="border-bottom:1px solid rgba(255,255,255,0.1)">
                    <th style="text-align:left; padding:6px; color:#64748b; font-weight:700">Taryfa</th>
                    <th style="text-align:center; padding:6px; color:#64748b">2000 kWh</th>
                    <th style="text-align:center; padding:6px; color:#64748b">4000 kWh</th>
                    <th style="text-align:center; padding:6px; color:#64748b">6000 kWh</th>
                    <th style="text-align:center; padding:6px; color:#64748b">Godziny taniej</th>
                  </tr>
                </thead>
                <tbody>
                  <tr style="border-bottom:1px solid rgba(255,255,255,0.05)">
                    <td style="padding:6px; font-weight:700">G11</td>
                    <td style="text-align:center; padding:6px">1.22 zł</td>
                    <td style="text-align:center; padding:6px">1.12 zł</td>
                    <td style="text-align:center; padding:6px">1.07 zł</td>
                    <td style="text-align:center; padding:6px; color:#94a3b8">brak — stała</td>
                  </tr>
                  <tr style="border-bottom:1px solid rgba(255,255,255,0.05)">
                    <td style="padding:6px; font-weight:700">G12</td>
                    <td style="text-align:center; padding:6px">0.97 zł</td>
                    <td style="text-align:center; padding:6px">0.87 zł</td>
                    <td style="text-align:center; padding:6px">0.82 zł</td>
                    <td style="text-align:center; padding:6px; color:#2ecc71">13–15 + 22–06</td>
                  </tr>
                  <tr style="border-bottom:1px solid rgba(255,255,255,0.05)">
                    <td style="padding:6px; font-weight:700">G12w</td>
                    <td style="text-align:center; padding:6px">0.99 zł</td>
                    <td style="text-align:center; padding:6px">0.90 zł</td>
                    <td style="text-align:center; padding:6px">0.85 zł</td>
                    <td style="text-align:center; padding:6px; color:#2ecc71">13–15 + 22–06 + weekendy</td>
                  </tr>
                  <tr style="background:rgba(46,204,113,0.06)">
                    <td style="padding:6px; font-weight:800; color:#2ecc71">G13 ✓</td>
                    <td style="text-align:center; padding:6px; font-weight:800; color:#2ecc71">0.97 zł</td>
                    <td style="text-align:center; padding:6px; font-weight:800; color:#2ecc71">0.87 zł</td>
                    <td style="text-align:center; padding:6px; font-weight:800; color:#2ecc71">0.83 zł</td>
                    <td style="text-align:center; padding:6px; color:#2ecc71; font-weight:700">85% off-peak + weekendy</td>
                  </tr>
                  <tr style="border-top:2px solid rgba(168,85,247,0.3); background:rgba(168,85,247,0.06)">
                    <td style="padding:6px; font-weight:800; color:#a855f7">⚡ Dynamiczna</td>
                    <td style="text-align:center; padding:6px; color:#a855f7">zmienna</td>
                    <td style="text-align:center; padding:6px; color:#a855f7">zmienna</td>
                    <td style="text-align:center; padding:6px; color:#a855f7">zmienna</td>
                    <td style="text-align:center; padding:6px; color:#a855f7; font-weight:700">cena co godzinę wg ENTSO-E</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div style="font-size:9px; color:#64748b; margin-top:6px; line-height:1.4">
              * Ceny brutto za kWh (sprzedaż + dystrybucja). G13: 5% przedpołudniowa, 10% popołudniowa, 85% off-peak.<br>
              Weekendy i święta = cały dzień off-peak (najtańsza strefa).
            </div>
          </div>

        </div>

        <!-- ═══════ TAB: BATTERY ═══════ -->
        <div class="tab-content" data-tab="battery">

          <!-- ROW 1: SOC + Live Parameters -->
          <div class="grid-cards gc-2" style="margin-bottom:12px">
            <div class="card">
              <div class="card-title">🔋 Stan baterii</div>
              <div style="display:flex; align-items:center; gap:16px; margin-bottom:10px">
                <div>
                  <div style="font-size:40px; font-weight:900; color:#fff" id="v-soc-tab">—%</div>
                  <div style="font-size:13px; color:#94a3b8" id="v-battery-energy-tab">— kWh dostępne</div>
                </div>
                <div style="flex:1">
                  <div class="soc-bar" style="height:16px; margin-bottom:4px"><div class="soc-fill" id="soc-fill-tab" style="width:0%"></div></div>
                  <div style="display:flex; justify-content:space-between; font-size:9px; color:#64748b">
                    <span>0%</span><span>50%</span><span>100%</span>
                  </div>
                </div>
              </div>
              <!-- Dynamic status -->
              <div style="display:flex; align-items:center; gap:8px; padding:8px 12px; border-radius:8px; margin-bottom:8px" id="batt-status-box">
                <div style="width:10px; height:10px; border-radius:50%; animation:pulse 1.5s infinite" id="batt-status-dot"></div>
                <div style="font-size:13px; font-weight:700" id="batt-status-text">STANDBY</div>
                <div style="font-size:13px; font-weight:800; margin-left:auto" id="batt-status-power">— W</div>
              </div>
              <div class="dr"><span class="lb">Czas pracy (est.)</span><span class="vl" id="v-battery-runtime-tab">—</span></div>
              <div class="dr"><span class="lb">Tryb falownika</span><span class="vl" id="v-work-mode-tab">—</span></div>
              <div class="dr"><span class="lb">Tryb baterii</span><span class="vl" id="v-batt-mode-tab">—</span></div>
            </div>

            <div class="card">
              <div class="card-title">⚡ Parametry na żywo</div>
              <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:10px">
                <div style="background:rgba(0,212,255,0.08); border-radius:10px; padding:10px; text-align:center">
                  <div style="font-size:9px; color:#00d4ff; text-transform:uppercase">Napięcie</div>
                  <div style="font-size:22px; font-weight:800; color:#fff; margin-top:2px" id="v-batt-v-tab">— V</div>
                </div>
                <div style="background:rgba(247,183,49,0.08); border-radius:10px; padding:10px; text-align:center">
                  <div style="font-size:9px; color:#f7b731; text-transform:uppercase">Prąd</div>
                  <div style="font-size:22px; font-weight:800; color:#fff; margin-top:2px" id="v-batt-a-tab">— A</div>
                </div>
                <div style="background:rgba(231,76,60,0.08); border-radius:10px; padding:10px; text-align:center">
                  <div style="font-size:9px; color:#e74c3c; text-transform:uppercase">Temperatura</div>
                  <div style="font-size:22px; font-weight:800; color:#fff; margin-top:2px" id="v-batt-temp-tab">— °C</div>
                </div>
                <div style="background:rgba(46,204,113,0.08); border-radius:10px; padding:10px; text-align:center">
                  <div style="font-size:9px; color:#2ecc71; text-transform:uppercase">Moc</div>
                  <div style="font-size:22px; font-weight:800; color:#fff; margin-top:2px" id="v-batt-p-tab">— W</div>
                </div>
              </div>
              <div class="dr"><span class="lb">DOD on-grid</span><span class="vl" id="v-batt-dod-tab">—%</span></div>
              <div class="dr"><span class="lb">Prąd ładowania (maks.)</span><span class="vl" id="v-batt-charge-rate-tab">— A</span></div>
              <div class="dr"><span class="lb">Pojemność nominalna</span><span class="vl">10.2 kWh (LiFePO4)</span></div>
            </div>
          </div>

          <!-- ROW 2: Daily Statistics -->
          <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(130px, 1fr)); gap:8px; margin-bottom:12px">
            <div class="card" style="text-align:center; padding:14px 8px">
              <div style="font-size:9px; color:#2ecc71; text-transform:uppercase; letter-spacing:0.5px">↑ Ładowanie dziś</div>
              <div style="font-size:22px; font-weight:800; color:#2ecc71; margin-top:4px" id="v-batt-charge-tab">— kWh</div>
            </div>
            <div class="card" style="text-align:center; padding:14px 8px">
              <div style="font-size:9px; color:#f39c12; text-transform:uppercase; letter-spacing:0.5px">↓ Rozładowanie dziś</div>
              <div style="font-size:22px; font-weight:800; color:#f39c12; margin-top:4px" id="v-batt-discharge-tab">— kWh</div>
            </div>
            <div class="card" style="text-align:center; padding:14px 8px">
              <div style="font-size:9px; color:#00d4ff; text-transform:uppercase; letter-spacing:0.5px">🔄 Cykli dziś</div>
              <div style="font-size:22px; font-weight:800; color:#00d4ff; margin-top:4px" id="v-batt-cycles-tab">—</div>
            </div>
            <div class="card" style="text-align:center; padding:14px 8px">
              <div style="font-size:9px; color:#a855f7; text-transform:uppercase; letter-spacing:0.5px">⚡ Sprawność</div>
              <div style="font-size:22px; font-weight:800; color:#a855f7; margin-top:4px" id="v-batt-efficiency-tab">—%</div>
            </div>
          </div>

          <!-- ROW 3: Battery Health -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">🏥 Zdrowie baterii</div>
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:10px; margin-top:8px">
              <div>
                <div class="dr"><span class="lb">Technologia</span><span class="vl" style="color:#2ecc71">LiFePO4 (Lynx Home U)</span></div>
                <div class="dr"><span class="lb">Pojemność</span><span class="vl">10.2 kWh</span></div>
                <div class="dr"><span class="lb">DOD maks.</span><span class="vl">95% (min SOC 5%)</span></div>
                <div class="dr"><span class="lb">Żywotność gwar.</span><span class="vl">6000+ cykli</span></div>
              </div>
              <div>
                <div class="dr"><span class="lb">Napięcie nominalne</span><span class="vl">51.2 V</span></div>
                <div class="dr"><span class="lb">Prąd ładowania maks.</span><span class="vl">18.5 A</span></div>
                <div class="dr"><span class="lb">Zakres temp. pracy</span><span class="vl">0°C – 50°C</span></div>
                <div class="dr"><span class="lb">Ostrzeżenia</span><span class="vl" id="v-batt-warning-tab" style="color:#2ecc71">Brak</span></div>
              </div>
            </div>
          </div>

          <!-- ROW 4: Expanded Arbitrage -->
          <div class="card" style="margin-bottom:12px" id="arbitrage-card">
            <div class="card-title">💰 Rozszerzony Arbitraż Energii</div>
            <div style="margin-bottom:12px">
              <div style="font-size:12px; color:#94a3b8; line-height:1.6">
                Arbitraż polega na ładowaniu baterii gdy energia jest najtańsza (noc, off-peak G13) i rozładowywaniu podczas szczytu popołudniowego lub sprzedaży po najwyższej cenie RCE.
              </div>
            </div>

            <!-- Arbitrage KPIs -->
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(130px, 1fr)); gap:8px; margin-bottom:12px">
              <div style="background:rgba(46,204,113,0.08); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#2ecc71; text-transform:uppercase">Potencjał zarobku</div>
                <div style="font-size:22px; font-weight:800; color:#2ecc71; margin-top:2px" id="v-arbitrage-tab">— zł</div>
                <div style="font-size:9px; color:#64748b">na cykl</div>
              </div>
              <div style="background:rgba(0,212,255,0.08); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#00d4ff; text-transform:uppercase">Spread RCE</div>
                <div style="font-size:22px; font-weight:800; color:#00d4ff; margin-top:2px" id="v-arb-spread">— zł</div>
                <div style="font-size:9px; color:#64748b">max−min dziś</div>
              </div>
              <div style="background:rgba(247,183,49,0.08); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#f7b731; text-transform:uppercase">Cena ładowania</div>
                <div style="font-size:22px; font-weight:800; color:#f7b731; margin-top:2px" id="v-arb-buy-price">— zł</div>
                <div style="font-size:9px; color:#64748b">zł/kWh off-peak</div>
              </div>
              <div style="background:rgba(231,76,60,0.08); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#e74c3c; text-transform:uppercase">Cena sprzedaży</div>
                <div style="font-size:22px; font-weight:800; color:#e74c3c; margin-top:2px" id="v-arb-sell-price">— zł</div>
                <div style="font-size:9px; color:#64748b">zł/kWh peak</div>
              </div>
            </div>

            <!-- Tariff Strategy (dynamic) -->
            <div style="background:rgba(255,255,255,0.03); border-radius:10px; padding:12px; margin-bottom:12px">
              <div style="font-size:11px; font-weight:700; color:#f7b731; margin-bottom:6px" id="arb-strategy-title">📋 Strategia G13 + RCE</div>
              <div style="font-size:11px; color:#cbd5e1; line-height:1.6" id="arb-strategy-body">
                <strong style="color:#2ecc71">⏰ 22:00–06:00</strong> — Ładuj baterię (off-peak, najtańsza strefa)<br>
                <strong style="color:#e74c3c">⏰ 07:00–13:00</strong> — Rozładowuj na dom (peak poranny)<br>
                <strong style="color:#f7b731">⏰ 13:00–17:00</strong> — PV ładuje baterię + eksport nadwyżki<br>
                <strong style="color:#e74c3c">⏰ 17:00–22:00</strong> — Rozładowuj na dom (peak wieczorny, najdrożej!)
              </div>
            </div>

            <!-- Automatyczny arbitraż — Premium feature -->
            <div style="background:linear-gradient(135deg, rgba(247,183,49,0.08), rgba(231,76,60,0.05)); border:1px solid rgba(247,183,49,0.2); border-radius:12px; padding:16px" id="arb-premium-box">
              <div style="display:flex; align-items:center; gap:8px; margin-bottom:8px">
                <div style="font-size:18px">🚀</div>
                <div style="font-size:13px; font-weight:800; color:#f7b731">Automatyczny Arbitraż AI</div>
                <div style="font-size:9px; background:#f7b731; color:#000; padding:2px 6px; border-radius:4px; font-weight:700; margin-left:auto" id="arb-premium-badge">PREMIUM</div>
              </div>
              <div style="font-size:11px; color:#cbd5e1; line-height:1.7; margin-bottom:10px" id="arb-premium-desc">
                <div style="margin-bottom:6px">Automatyczny system arbitrażu oparty na AI analizuje:</div>
                <div>✅ Prognozę cen RCE na 24h do przodu</div>
                <div>✅ Prognozę produkcji PV (Forecast.Solar)</div>
                <div>✅ Wzorce zużycia domu (ML)</div>
                <div>✅ Optymalizację cykli DOD dla żywotności baterii</div>
                <div>✅ Automatyczne przełączanie trybów falownika</div>
                <div>✅ Raport oszczędności i rekomendacje</div>
              </div>
              <div id="arb-premium-cta" style="display:none; text-align:center; margin-top:8px">
                <button style="background:linear-gradient(135deg,#f7b731,#e74c3c); color:#fff; border:none; border-radius:8px; padding:10px 24px; font-size:12px; font-weight:700; cursor:pointer; letter-spacing:0.5px">
                  ⭐ Przejdź na PREMIUM — odblokuj pełny arbitraż
                </button>
                <div style="font-size:9px; color:#64748b; margin-top:4px">Aktywacja przez klucz licencyjny w Ustawieniach</div>
              </div>
              <div id="arb-premium-active" style="display:none">
                <div class="dr"><span class="lb">Status automatyzacji</span><span class="vl" style="color:#2ecc71" id="v-arb-auto-status">Aktywny ✅</span></div>
                <div class="dr"><span class="lb">Następna akcja</span><span class="vl" id="v-arb-next-action">—</span></div>
                <div class="dr"><span class="lb">Zarobek z arbitrażu dziś</span><span class="vl" style="color:#2ecc71" id="v-arb-profit-today">— zł</span></div>
              </div>
            </div>
          </div>

        </div>

        <!-- ═══════ TAB: HEMS ═══════ -->
        <div class="tab-content" data-tab="hems">

          <!-- ═══ HEMS HEADER STRIP ═══ -->
          <div class="hems-header-strip">
            <div class="hems-h-title">🏗️ Autonomiczny System Zarządzania Energią</div>
            <div class="hems-h-badges">
              <span class="hems-h-badge active" id="hems-arb-status">● AKTYWNY</span>
              <span class="hems-h-badge mode" id="hems-arb-mode">AUTO</span>
              <span class="hems-h-badge tier" id="hems-arb-tier">PRO</span>
            </div>
          </div>

          <!-- ═══ KPI STRIP ═══ -->
          <div class="hems-kpis">
            <div class="hems-kpi">
              <div class="kpi-label">🔋 SOC</div>
              <div class="kpi-val" id="hems-kpi-soc" style="color:#2ecc71">—%</div>
            </div>
            <div class="hems-kpi">
              <div class="kpi-label">💰 RCE teraz</div>
              <div class="kpi-val" id="hems-kpi-rce" style="color:#00d4ff">— zł</div>
            </div>
            <div class="hems-kpi">
              <div class="kpi-label" id="hems-kpi-g13-label">⏰ Strefa G13</div>
              <div class="kpi-val" id="hems-kpi-g13" style="color:#f7b731">—</div>
            </div>
            <div class="hems-kpi">
              <div class="kpi-label">⚡ Tryb falownika</div>
              <div class="kpi-val" id="hems-kpi-inv" style="color:#a0aec0; font-size:14px">—</div>
            </div>
          </div>

          <!-- ═══ MANUAL CONTROLS ═══ -->
          <div class="card" style="margin-bottom:14px">
            <div class="card-title">⚙️ Sterowanie Ręczne Trybem HEMS</div>
            <div class="actions">
              <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','set_mode',{mode:'auto'})">🔄 Tryb Auto</button>
              <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','set_mode',{mode:'sell'})">💰 Max Sprzedaż</button>
              <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','set_mode',{mode:'charge'})">🔋 Tryb Charge</button>
              <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','set_mode',{mode:'peak_save'})">🏠 Szczyt</button>
            </div>
            <div class="actions" style="margin-top:6px">
              <button class="action-btn" style="background:rgba(46,204,113,0.15);border:1px solid #2ecc71;color:#2ecc71" onclick="this.getRootNode().host._executeForceAction('charge')">🔋 Wymuś Ładow.</button>
              <button class="action-btn" style="background:rgba(231,76,60,0.15);border:1px solid #e74c3c;color:#e74c3c" onclick="this.getRootNode().host._executeForceAction('discharge')">⚡ Wymuś Rozład.</button>
              <button class="action-btn" style="background:rgba(231,76,60,0.2);border:2px solid #e74c3c;color:#e74c3c;font-weight:900" onclick="this.getRootNode().host._executeForceAction('emergency_stop')">🚨 STOP</button>
            </div>
          </div>

          <!-- ═══ AI ADVISOR ═══ -->
          <div class="card" style="margin-bottom:14px">
            <div class="card-title">🤖 AI Energy Advisor</div>
            <div style="display:flex; gap:10px; align-items:flex-start;">
              <div style="font-size:28px; filter:drop-shadow(0 0 5px rgba(0,212,255,0.5));">🤖</div>
              <div class="recommendation" style="flex:1" id="v-hems-rec-hems">Ładowanie porady z asystenta...</div>
            </div>
          </div>

          <!-- ═══ W0: GRID IMPORT GUARD (NADRZĘDNA OCHRONA) ═══ -->
          <div class="hems-layer" id="hems-layer-w0">
            <div class="hems-layer-header" onclick="this.getRootNode().host._toggleHEMSSection('w0')">
              <div class="hl-left">
                <span class="hl-tag" style="background:rgba(231,76,60,0.2);color:#e74c3c">W0</span>
                <span class="hl-name">Grid Import Guard</span>
                <span style="font-size:10px;color:#64748b" id="hems-w0-count">2 automatyzacje</span>
              </div>
              <span class="hl-chevron">▼</span>
            </div>
            <div class="hems-layer-body" id="hems-w0-body">
              <!-- Grid Import Guard -->
              <div class="hems-auto-card" id="hac-grid-guard">
                <div class="hac-top"><span class="hac-icon">🛡️</span><span class="hac-name">Grid Import Guard</span><span class="hac-status" id="hac-grid-guard-st">—</span></div>
                <div class="hac-desc">STOP ładowania baterii z sieci w drogich godzinach. Wyjątek: RCE &lt; 100 PLN/MWh.</div>
                <div class="hac-sensors">
                  <span class="hs-label">Grid:</span><span class="hs-val" id="hac-gg-grid">—</span>
                  <span class="hs-label">Bat.:</span><span class="hs-val" id="hac-gg-bat">—</span>
                </div>
              </div>
              <!-- PV Surplus Smart Charge -->
              <div class="hems-auto-card" id="hac-pv-surplus">
                <div class="hac-top"><span class="hac-icon">☀️🔋</span><span class="hac-name">PV Surplus → ładuj baterię</span><span class="hac-status" id="hac-pv-surplus-st">—</span></div>
                <div class="hac-desc">Nadwyżka PV (export &gt;300W) w drogich godz. → ładuj baterię. Guard czuwa nad importem.</div>
                <div class="hac-sensors">
                  <span class="hs-label">Grid:</span><span class="hs-val" id="hac-ps-grid">—</span>
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-ps-soc">—</span>
                </div>
              </div>
            </div>
          </div>

          <!-- ═══ W1: HARMONOGRAM G13 ═══ -->
          <div class="hems-layer" id="hems-layer-w1">
            <div class="hems-layer-header" onclick="this.getRootNode().host._toggleHEMSSection('w1')">
              <div class="hl-left">
                <span class="hl-tag" style="background:rgba(230,126,34,0.15);color:#e67e22">W1</span>
                <span class="hl-name" id="hems-w1-name">Harmonogram G13</span>
                <span style="font-size:10px;color:#64748b" id="hems-w1-count">5 automatyzacji</span>
              </div>
              <span class="hl-chevron">▼</span>
            </div>
            <div class="hems-layer-body" id="hems-w1-body">
              <!-- Sprzedaż 07:00 -->
              <div class="hems-auto-card" id="hac-morning-sell">
                <div class="hac-top"><span class="hac-icon">☀️</span><span class="hac-name">Sprzedaż (07:00)</span><span class="hac-status" id="hac-morning-sell-st">—</span></div>
                <div class="hac-desc" id="hac-morning-sell-desc">G13 szczyt poranny (0.91 zł). Sprzedawaj 7-13 Pn-Pt.</div>
                <div class="hac-sensors">
                  <span class="hs-label">RCE:</span><span class="hs-val" id="hac-ms-rce">—</span>
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-ms-soc">—</span>
                </div>
              </div>
              <!-- Ładowanie 13:00 -->
              <div class="hems-auto-card" id="hac-midday-charge">
                <div class="hac-top"><span class="hac-icon">🔋</span><span class="hac-name">Ładowanie (13:00)</span><span class="hac-status" id="hac-midday-charge-st">—</span></div>
                <div class="hac-desc" id="hac-midday-charge-desc">Off-peak (0.63 zł). Ładuj baterię 13:00-szczyt Pn-Pt.</div>
                <div class="hac-sensors">
                  <span class="hs-label">RCE:</span><span class="hs-val" id="hac-mc-rce">—</span>
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-mc-soc">—</span>
                </div>
              </div>
              <!-- Szczyt wieczorny -->
              <div class="hems-auto-card" id="hac-evening-peak">
                <div class="hac-top"><span class="hac-icon">💰</span><span class="hac-name">Szczyt wieczorny</span><span class="hac-status" id="hac-evening-peak-st">—</span></div>
                <div class="hac-desc" id="hac-evening-peak-desc">G13 szczyt (1.50 zł). Bateria zasila dom.</div>
                <div class="hac-sensors">
                  <span class="hs-label">G13:</span><span class="hs-val" id="hac-ep-g13">—</span>
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-ep-soc">—</span>
                </div>
              </div>
              <!-- Weekend -->
              <div class="hems-auto-card" id="hac-weekend">
                <div class="hac-top"><span class="hac-icon">🏖️</span><span class="hac-name">Weekend</span><span class="hac-status" id="hac-weekend-st">—</span></div>
                <div class="hac-desc">Off-peak cały dzień → autokonsumpcja.</div>
                <div class="hac-sensors">
                  <span class="hs-label">RCE:</span><span class="hs-val" id="hac-wk-rce">—</span>
                  <span class="hs-label">Najtaniej:</span><span class="hs-val" id="hac-wk-cheap">—</span>
                </div>
              </div>
              <!-- Arbitraż nocny -->
              <div class="hems-auto-card" id="hac-night-arb">
                <div class="hac-top"><span class="hac-icon">🌙</span><span class="hac-name">Arbitraż nocny</span><span class="hac-status" id="hac-night-arb-st">—</span></div>
                <div class="hac-desc">Nocne ładowanie z sieci (0.63 zł → 1.50 zł) gdy słaba prognoza PV.</div>
                <div class="hac-sensors">
                  <span class="hs-label">Prognoza:</span><span class="hs-val" id="hac-na-fcst">—</span>
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-na-soc">—</span>
                </div>
              </div>
            </div>
          </div>

          <!-- ═══ W2: RCE DYNAMICZNE ═══ -->
          <div class="hems-layer" id="hems-layer-w2">
            <div class="hems-layer-header" onclick="this.getRootNode().host._toggleHEMSSection('w2')">
              <div class="hl-left">
                <span class="hl-tag" style="background:rgba(0,212,255,0.15);color:#00d4ff">W2</span>
                <span class="hl-name">RCE Dynamiczne</span>
                <span style="font-size:10px;color:#64748b" id="hems-w2-count">6 automatyzacji</span>
              </div>
              <span class="hl-chevron">▼</span>
            </div>
            <div class="hems-layer-body" id="hems-w2-body">
              <!-- Najtańsze okno -->
              <div class="hems-auto-card" id="hac-rce-cheap">
                <div class="hac-top"><span class="hac-icon">🟢</span><span class="hac-name">Najtańsze okno → ładuj</span><span class="hac-status" id="hac-rce-cheap-st">—</span></div>
                <div class="hac-desc">Binary sensor PSE: najtańsze okno aktywne → ładuj baterię.</div>
                <div class="hac-sensors">
                  <span class="hs-label">Okno:</span><span class="hs-val" id="hac-rc-win">—</span>
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-rc-soc">—</span>
                </div>
              </div>
              <!-- Najdroższe okno -->
              <div class="hems-auto-card" id="hac-rce-exp">
                <div class="hac-top"><span class="hac-icon">🔴</span><span class="hac-name">Najdroższe okno → alert</span><span class="hac-status" id="hac-rce-exp-st">—</span></div>
                <div class="hac-desc">Najdroższe okno aktywne → bateria zasila dom, max oszczędność.</div>
                <div class="hac-sensors">
                  <span class="hs-label">RCE:</span><span class="hs-val" id="hac-re-rce">—</span>
                  <span class="hs-label">G13:</span><span class="hs-val" id="hac-re-g13">—</span>
                </div>
              </div>
              <!-- Niska cena -->
              <div class="hems-auto-card" id="hac-rce-low">
                <div class="hac-top"><span class="hac-icon">📉</span><span class="hac-name">Niska cena → ładuj</span><span class="hac-status" id="hac-rce-low-st">—</span></div>
                <div class="hac-desc">RCE &lt; 150 PLN/MWh → nie opłaca się sprzedawać. Ładuj baterię.</div>
                <div class="hac-sensors">
                  <span class="hs-label">RCE (MWh):</span><span class="hs-val" id="hac-rl-mwh">—</span>
                  <span class="hs-label">Trend:</span><span class="hs-val" id="hac-rl-trend">—</span>
                </div>
              </div>
              <!-- Cena wzrosła -->
              <div class="hems-auto-card" id="hac-rce-high">
                <div class="hac-top"><span class="hac-icon">📈</span><span class="hac-name">Cena wzrosła → sprzedaj</span><span class="hac-status" id="hac-rce-high-st">—</span></div>
                <div class="hac-desc">RCE &gt; 300 PLN/MWh → opłaca się sprzedawać.</div>
                <div class="hac-sensors">
                  <span class="hs-label">RCE (MWh):</span><span class="hs-val" id="hac-rh-mwh">—</span>
                  <span class="hs-label">Trend:</span><span class="hs-val" id="hac-rh-trend">—</span>
                </div>
              </div>
              <!-- Peak wieczorny -->
              <div class="hems-auto-card" id="hac-rce-peak">
                <div class="hac-top"><span class="hac-icon">💰💰</span><span class="hac-name">RCE Peak + G13 Szczyt</span><span class="hac-status" id="hac-rce-peak-st">—</span></div>
                <div class="hac-desc">RCE &gt; 500 PLN/MWh wieczorem + G13 szczyt → max zysk!</div>
                <div class="hac-sensors">
                  <span class="hs-label">RCE:</span><span class="hs-val" id="hac-rp-rce">—</span>
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-rp-soc">—</span>
                </div>
              </div>
              <!-- Ujemna cena -->
              <div class="hems-auto-card" id="hac-rce-neg">
                <div class="hac-top"><span class="hac-icon">🤑</span><span class="hac-name">Ujemna cena → DARMOWA!</span><span class="hac-status" id="hac-rce-neg-st">—</span></div>
                <div class="hac-desc">RCE ujemna → darmowa energia! Ładuj baterię + bojler ON.</div>
                <div class="hac-sensors">
                  <span class="hs-label">RCE (MWh):</span><span class="hs-val" id="hac-rn-mwh">—</span>
                  <span class="hs-label">Bojler:</span><span class="hs-val" id="hac-rn-blr">—</span>
                </div>
              </div>
            </div>
          </div>

          <!-- ═══ W3: BEZPIECZEŃSTWO SOC ═══ -->
          <div class="hems-layer" id="hems-layer-w3">
            <div class="hems-layer-header" onclick="this.getRootNode().host._toggleHEMSSection('w3')">
              <div class="hl-left">
                <span class="hl-tag" style="background:rgba(231,76,60,0.15);color:#e74c3c">W3</span>
                <span class="hl-name">Bezpieczeństwo SOC</span>
                <span style="font-size:10px;color:#64748b" id="hems-w3-count">5 automatyzacji</span>
              </div>
              <span class="hl-chevron">▼</span>
            </div>
            <div class="hems-layer-body" id="hems-w3-body">
              <!-- SOC 11:00 -->
              <div class="hems-auto-card" id="hac-soc-11">
                <div class="hac-top"><span class="hac-icon">⚠️</span><span class="hac-name">SOC check 11:00 (PV-only)</span><span class="hac-status" id="hac-soc-11-st">—</span></div>
                <div class="hac-desc">SOC &lt; 50% o 11:00 → ładuj z nadwyżki PV. NIE z sieci w szczycie!</div>
                <div class="hac-sensors">
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-s11-soc">—</span>
                  <span class="hs-label">Próg:</span><span class="hs-val">&lt;50%</span>
                </div>
              </div>
              <!-- SOC 12:00 -->
              <div class="hems-auto-card" id="hac-soc-12">
                <div class="hac-top"><span class="hac-icon">⚠️</span><span class="hac-name">SOC check 12:00 (ostatnia szansa)</span><span class="hac-status" id="hac-soc-12-st">—</span></div>
                <div class="hac-desc">SOC &lt; 70% o 12:00. Nadwyżka PV → bateria. Brak → czekaj na 13:00 off-peak.</div>
                <div class="hac-sensors">
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-s12-soc">—</span>
                  <span class="hs-label">Próg:</span><span class="hs-val">&lt;70%</span>
                </div>
              </div>
              <!-- Smart SOC Protection (tariff-aware) -->
              <div class="hems-auto-card" id="hac-soc-low">
                <div class="hac-top"><span class="hac-icon">🔋</span><span class="hac-name">Smart SOC Protection (tariff-aware)</span><span class="hac-status" id="hac-soc-low-st">—</span></div>
                <div class="hac-desc">SOC &lt; 20%: drogie godz. → PV-only, tanie godz. → ładuj normalnie.</div>
                <div class="hac-sensors">
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-sl-soc">—</span>
                  <span class="hs-label">Próg:</span><span class="hs-val">&lt;20%</span>
                </div>
              </div>
              <!-- Emergency SOC < 5% -->
              <div class="hems-auto-card" id="hac-soc-emergency">
                <div class="hac-top"><span class="hac-icon">🚨</span><span class="hac-name">EMERGENCY SOC &lt; 5%</span><span class="hac-status" id="hac-soc-emergency-st">—</span></div>
                <div class="hac-desc">Ładuj awaryjnie NIEZALEŻNIE od taryfy do 15%! Bateria bliska shutdown.</div>
                <div class="hac-sensors">
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-se-soc">—</span>
                  <span class="hs-label">Próg:</span><span class="hs-val">&lt;5%</span>
                </div>
              </div>
              <!-- Słaba prognoza -->
              <div class="hems-auto-card" id="hac-weak-fcst">
                <div class="hac-top"><span class="hac-icon">🌧️</span><span class="hac-name">Słaba prognoza → DOD</span><span class="hac-status" id="hac-weak-fcst-st">—</span></div>
                <div class="hac-desc">Jutro &lt; 5 kWh → zachowaj baterię (DOD → 70%).</div>
                <div class="hac-sensors">
                  <span class="hs-label">Jutro:</span><span class="hs-val" id="hac-wf-fcst">—</span>
                  <span class="hs-label">DOD:</span><span class="hs-val" id="hac-wf-dod">—</span>
                </div>
              </div>
              <!-- Przywróć DOD -->
              <div class="hems-auto-card" id="hac-restore-dod">
                <div class="hac-top"><span class="hac-icon">☀️</span><span class="hac-name">Przywróć DOD</span><span class="hac-status" id="hac-restore-dod-st">—</span></div>
                <div class="hac-desc">PV &gt; 500W przez 10 min → DOD z powrotem na 95%.</div>
                <div class="hac-sensors">
                  <span class="hs-label">PV:</span><span class="hs-val" id="hac-rd-pv">—</span>
                  <span class="hs-label">Próg:</span><span class="hs-val">&gt;500W</span>
                </div>
              </div>
            </div>
          </div>

          <!-- ═══ W4: KASKADY NAPIĘCIA + NADWYŻKI PV ═══ -->
          <div class="hems-layer collapsed" id="hems-layer-w4">
            <div class="hems-layer-header" onclick="this.getRootNode().host._toggleHEMSSection('w4')">
              <div class="hl-left">
                <span class="hl-tag" style="background:rgba(155,89,182,0.15);color:#9b59b6">W4</span>
                <span class="hl-name">Kaskady napięcia + nadwyżki PV</span>
                <span style="font-size:10px;color:#64748b" id="hems-w4-count">10 automatyzacji</span>
              </div>
              <span class="hl-chevron">▼</span>
            </div>
            <div class="hems-layer-body" id="hems-w4-body">
              <!-- Napięcie: Bojler -->
              <div class="hems-auto-card" id="hac-volt-blr">
                <div class="hac-top"><span class="hac-icon">⚡</span><span class="hac-name">Napięcie → Bojler</span><span class="hac-status" id="hac-volt-blr-st">—</span></div>
                <div class="hac-desc">&gt;252V → Bojler ON (zagospodarowanie nadwyżki).</div>
                <div class="hac-sensors">
                  <span class="hs-label">V max:</span><span class="hs-val" id="hac-vb-vmax">—</span>
                  <span class="hs-label">Bojler:</span><span class="hs-val" id="hac-vb-blr">—</span>
                </div>
              </div>
              <!-- Napięcie: Klima -->
              <div class="hems-auto-card" id="hac-volt-ac">
                <div class="hac-top"><span class="hac-icon">⚡⚡</span><span class="hac-name">Napięcie → Klima</span><span class="hac-status" id="hac-volt-ac-st">—</span></div>
                <div class="hac-desc">&gt;253V → Klima ON (bojler już działa).</div>
                <div class="hac-sensors">
                  <span class="hs-label">V max:</span><span class="hs-val" id="hac-va-vmax">—</span>
                  <span class="hs-label">Klima:</span><span class="hs-val" id="hac-va-ac">—</span>
                </div>
              </div>
              <!-- Napięcie: Ładuj baterię -->
              <div class="hems-auto-card" id="hac-volt-chrg">
                <div class="hac-top"><span class="hac-icon">🔴</span><span class="hac-name">Krytyczne napięcie</span><span class="hac-status" id="hac-volt-chrg-st">—</span></div>
                <div class="hac-desc">&gt;254V → Ładuj baterię natychmiast!</div>
                <div class="hac-sensors">
                  <span class="hs-label">V max:</span><span class="hs-val" id="hac-vc-vmax">—</span>
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-vc-soc">—</span>
                </div>
              </div>
              <!-- Nadwyżka: Bojler -->
              <div class="hems-auto-card" id="hac-sur-blr">
                <div class="hac-top"><span class="hac-icon">☀️</span><span class="hac-name">Nadwyżka → Bojler</span><span class="hac-status" id="hac-sur-blr-st">—</span></div>
                <div class="hac-desc">&gt;2kW nadwyżki + SOC &gt;80% → Bojler ON.</div>
                <div class="hac-sensors">
                  <span class="hs-label">Nadwyżka:</span><span class="hs-val" id="hac-sb-sur">—</span>
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-sb-soc">—</span>
                </div>
              </div>
              <!-- Nadwyżka: Klima -->
              <div class="hems-auto-card" id="hac-sur-ac">
                <div class="hac-top"><span class="hac-icon">❄️</span><span class="hac-name">Nadwyżka → Klima</span><span class="hac-status" id="hac-sur-ac-st">—</span></div>
                <div class="hac-desc">&gt;3kW nadwyżki + SOC &gt;85% + bojler ON → Klima ON.</div>
                <div class="hac-sensors">
                  <span class="hs-label">Nadwyżka:</span><span class="hs-val" id="hac-sa-sur">—</span>
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-sa-soc">—</span>
                </div>
              </div>
              <!-- Nadwyżka: Gniazdko -->
              <div class="hems-auto-card" id="hac-sur-sock">
                <div class="hac-top"><span class="hac-icon">🔌</span><span class="hac-name">Nadwyżka → Gniazdko 2</span><span class="hac-status" id="hac-sur-sock-st">—</span></div>
                <div class="hac-desc">&gt;4kW nadwyżki + SOC &gt;90% + bojler + klima → Gniazdko ON.</div>
                <div class="hac-sensors">
                  <span class="hs-label">Nadwyżka:</span><span class="hs-val" id="hac-ss-sur">—</span>
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-ss-soc">—</span>
                </div>
              </div>
              <!-- Awaryjne OFF -->
              <div class="hems-auto-card" id="hac-emergency">
                <div class="hac-top"><span class="hac-icon">🚨</span><span class="hac-name">Awaryjne OFF</span><span class="hac-status" id="hac-emergency-st">—</span></div>
                <div class="hac-desc">SOC &lt; 50% → wyłącz wszystkie obciążenia.</div>
                <div class="hac-sensors">
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-em-soc">—</span>
                  <span class="hs-label">Próg:</span><span class="hs-val">&lt;50%</span>
                </div>
              </div>
            </div>
          </div>

          <!-- ═══ W5: SMART PRE-PEAK ═══ -->
          <div class="hems-layer collapsed" id="hems-layer-w5">
            <div class="hems-layer-header" onclick="this.getRootNode().host._toggleHEMSSection('w5')">
              <div class="hl-left">
                <span class="hl-tag" style="background:rgba(52,152,219,0.15);color:#3498db">W5</span>
                <span class="hl-name">Smart Pre-Peak (pogoda + PV)</span>
                <span style="font-size:10px;color:#64748b" id="hems-w5-count">6 punktów kontrolnych</span>
              </div>
              <span class="hl-chevron">▼</span>
            </div>
            <div class="hems-layer-body" id="hems-w5-body">
              <!-- 05:30 -->
              <div class="hems-auto-card" id="hac-pp-0530">
                <div class="hac-top"><span class="hac-icon">🌧️⚡</span><span class="hac-name">05:30 poranny check</span><span class="hac-status" id="hac-pp-0530-st">—</span></div>
                <div class="hac-desc">SOC &lt;80% + PV &lt;10kWh → ładuj z sieci (0.63 zł) do 07:00.</div>
                <div class="hac-sensors">
                  <span class="hs-label">PV prognoza:</span><span class="hs-val" id="hac-p5-fcst">—</span>
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-p5-soc">—</span>
                </div>
              </div>
              <!-- 10:00 -->
              <div class="hems-auto-card" id="hac-pp-1000">
                <div class="hac-top"><span class="hac-icon">🌥️</span><span class="hac-name">10:00 weryfikacja Ecowitt</span><span class="hac-status" id="hac-pp-1000-st">—</span></div>
                <div class="hac-desc">SOC &lt;60% + niska radiacja → priorytet PV→bateria.</div>
                <div class="hac-sensors">
                  <span class="hs-label">Radiacja:</span><span class="hs-val" id="hac-p10-rad">—</span>
                  <span class="hs-label">UV:</span><span class="hs-val" id="hac-p10-uv">—</span>
                </div>
              </div>
              <!-- 13:30 -->
              <div class="hems-auto-card" id="hac-pp-1330">
                <div class="hac-top"><span class="hac-icon">⚠️🔋</span><span class="hac-name">13:30 ostatnia szansa (zima)</span><span class="hac-status" id="hac-pp-1330-st">—</span></div>
                <div class="hac-desc">SOC &lt;80% + PV rem &lt;5kWh → ładuj! Szczyt za 2.5h.</div>
                <div class="hac-sensors">
                  <span class="hs-label">PV rem.:</span><span class="hs-val" id="hac-p13-rem">—</span>
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-p13-soc">—</span>
                </div>
              </div>
              <!-- 18:00 -->
              <div class="hems-auto-card" id="hac-pp-1800">
                <div class="hac-top"><span class="hac-icon">☀️⚠️</span><span class="hac-name">18:00 pre-peak lato</span><span class="hac-status" id="hac-pp-1800-st">—</span></div>
                <div class="hac-desc">SOC &lt;70% + słaba radiacja → ładuj! Szczyt o 19:00 (lato).</div>
                <div class="hac-sensors">
                  <span class="hs-label">Radiacja:</span><span class="hs-val" id="hac-p18-rad">—</span>
                  <span class="hs-label">SOC:</span><span class="hs-val" id="hac-p18-soc">—</span>
                </div>
              </div>
              <!-- Nagłe zachmurzenie -->
              <div class="hems-auto-card" id="hac-pp-cloud">
                <div class="hac-top"><span class="hac-icon">☁️</span><span class="hac-name">Nagłe zachmurzenie</span><span class="hac-status" id="hac-pp-cloud-st">—</span></div>
                <div class="hac-desc">Radiacja &lt;50 W/m² + SOC &lt;70% → priorytet bateria.</div>
                <div class="hac-sensors">
                  <span class="hs-label">Radiacja:</span><span class="hs-val" id="hac-pc-rad">—</span>
                  <span class="hs-label">PV:</span><span class="hs-val" id="hac-pc-pv">—</span>
                </div>
              </div>
              <!-- Deszcz -->
              <div class="hems-auto-card" id="hac-pp-rain">
                <div class="hac-top"><span class="hac-icon">🌧️</span><span class="hac-name">Deszcz → priorytet bateria</span><span class="hac-status" id="hac-pp-rain-st">—</span></div>
                <div class="hac-desc">Opady &gt;0.5mm/h + SOC &lt;70% → cały PV → bateria.</div>
                <div class="hac-sensors">
                  <span class="hs-label">Deszcz:</span><span class="hs-val" id="hac-pr-rain">—</span>
                  <span class="hs-label">PV:</span><span class="hs-val" id="hac-pr-pv">—</span>
                </div>
              </div>
            </div>
          </div>

          <!-- ═══ INNE AUTOMATYZACJE ═══ -->
          <div class="hems-layer collapsed" id="hems-layer-other">
            <div class="hems-layer-header" onclick="this.getRootNode().host._toggleHEMSSection('other')">
              <div class="hl-left">
                <span class="hl-tag" style="background:rgba(149,165,166,0.15);color:#95a5a6">+</span>
                <span class="hl-name">Inne automatyzacje</span>
                <span style="font-size:10px;color:#64748b" id="hems-other-count">4 automatyzacje</span>
              </div>
              <span class="hl-chevron">▼</span>
            </div>
            <div class="hems-layer-body" id="hems-other-body">
              <!-- Boiler harmonogram -->
              <div class="hems-auto-card" id="hac-boiler">
                <div class="hac-top"><span class="hac-icon">🔥</span><span class="hac-name">Harmonogram boilera</span><span class="hac-status" id="hac-boiler-st">—</span></div>
                <div class="hac-desc">Automatyczne wł/wył: 06/07, 14/15, 17/18.</div>
                <div class="hac-sensors">
                  <span class="hs-label">Bojler:</span><span class="hs-val" id="hac-bl-state">—</span>
                  <span class="hs-label">Czas:</span><span class="hs-val" id="hac-bl-time">—</span>
                </div>
              </div>
              <!-- Pompa piwnica -->
              <div class="hems-auto-card" id="hac-pump">
                <div class="hac-top"><span class="hac-icon">💧</span><span class="hac-name">Pompa piwnica</span><span class="hac-status" id="hac-pump-st">—</span></div>
                <div class="hac-desc">Czujnik zalania → pompa ON/OFF z 1 min opóźnieniem.</div>
                <div class="hac-sensors">
                  <span class="hs-label">Czujnik:</span><span class="hs-val" id="hac-pm-sensor">—</span>
                  <span class="hs-label">Pompa:</span><span class="hs-val" id="hac-pm-pump">—</span>
                </div>
              </div>
              <!-- Raport dobowy -->
              <div class="hems-auto-card" id="hac-report">
                <div class="hac-top"><span class="hac-icon">📊</span><span class="hac-name">Raport dobowy (21:00)</span><span class="hac-status" id="hac-report-st">—</span></div>
                <div class="hac-desc">Codzienny raport PV, dom, export, import, bilans, RCE avg.</div>
                <div class="hac-sensors">
                  <span class="hs-label">PV dziś:</span><span class="hs-val" id="hac-rpt-pv">—</span>
                  <span class="hs-label">Bilans:</span><span class="hs-val" id="hac-rpt-bal">—</span>
                </div>
              </div>
              <!-- Nierównowaga faz -->
              <div class="hems-auto-card" id="hac-phase">
                <div class="hac-top"><span class="hac-icon">⚖️</span><span class="hac-name">Nierównowaga faz</span><span class="hac-status" id="hac-phase-st">—</span></div>
                <div class="hac-desc">Różnica faz &gt; 3kW przez 10 min → alert.</div>
                <div class="hac-sensors">
                  <span class="hs-label">L1:</span><span class="hs-val" id="hac-ph-l1">—</span>
                  <span class="hs-label">L2:</span><span class="hs-val" id="hac-ph-l2">—</span>
                </div>
              </div>
            </div>
          </div>

        </div>

        <!-- ═══════ TAB: ROI / OPŁACALNOŚĆ ═══════ -->
        <div class="tab-content" data-tab="roi">

          <!-- Period selector -->
          <div style="display:flex; gap:6px; margin-bottom:16px; flex-wrap:wrap">
            <button class="tab-btn active" id="roi-period-day" onclick="this.getRootNode().host._switchRoiPeriod('day')" style="font-size:11px; padding:6px 14px">📅 Dzień</button>
            <button class="tab-btn" id="roi-period-week" onclick="this.getRootNode().host._switchRoiPeriod('week')" style="font-size:11px; padding:6px 14px">📆 Tydzień</button>
            <button class="tab-btn" id="roi-period-month" onclick="this.getRootNode().host._switchRoiPeriod('month')" style="font-size:11px; padding:6px 14px">🗓️ Miesiąc</button>
            <button class="tab-btn" id="roi-period-year" onclick="this.getRootNode().host._switchRoiPeriod('year')" style="font-size:11px; padding:6px 14px">📊 Rok</button>
          </div>

          <!-- ROW 1: Energy balance -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">⚡ Bilans energetyczny <span id="roi-period-label" style="color:#00d4ff; font-size:11px">(dziś)</span></div>
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:10px; margin-top:8px">
              <div style="background:rgba(247,183,49,0.1); border-radius:12px; padding:12px; text-align:center">
                <div style="font-size:10px; color:#f7b731; text-transform:uppercase; letter-spacing:1px">☀️ Produkcja PV</div>
                <div style="font-size:22px; font-weight:800; color:#f7b731; margin-top:4px" id="roi-pv">— kWh</div>
              </div>
              <div style="background:rgba(231,76,60,0.1); border-radius:12px; padding:12px; text-align:center">
                <div style="font-size:10px; color:#e74c3c; text-transform:uppercase; letter-spacing:1px">↓ Import z sieci</div>
                <div style="font-size:22px; font-weight:800; color:#e74c3c; margin-top:4px" id="roi-import">— kWh</div>
              </div>
              <div style="background:rgba(46,204,113,0.1); border-radius:12px; padding:12px; text-align:center">
                <div style="font-size:10px; color:#2ecc71; text-transform:uppercase; letter-spacing:1px">↑ Eksport do sieci</div>
                <div style="font-size:22px; font-weight:800; color:#2ecc71; margin-top:4px" id="roi-export">— kWh</div>
              </div>
              <div style="background:rgba(0,212,255,0.1); border-radius:12px; padding:12px; text-align:center">
                <div style="font-size:10px; color:#00d4ff; text-transform:uppercase; letter-spacing:1px">🏠 Zużycie własne</div>
                <div style="font-size:22px; font-weight:800; color:#00d4ff; margin-top:4px" id="roi-selfuse">— kWh</div>
              </div>
            </div>
          </div>

          <!-- ROW 2: Financial -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">💰 Finanse <span id="roi-fin-label" style="color:#00d4ff; font-size:11px">(dziś)</span></div>
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(160px, 1fr)); gap:10px; margin-top:8px">
              <div style="background:rgba(231,76,60,0.1); border-radius:12px; padding:14px; text-align:center">
                <div style="font-size:10px; color:#e74c3c; text-transform:uppercase; letter-spacing:1px">💸 Koszt importu</div>
                <div style="font-size:24px; font-weight:800; color:#e74c3c; margin-top:4px" id="roi-cost">— zł</div>
              </div>
              <div style="background:rgba(46,204,113,0.1); border-radius:12px; padding:14px; text-align:center">
                <div style="font-size:10px; color:#2ecc71; text-transform:uppercase; letter-spacing:1px">💵 Przychód eksport</div>
                <div style="font-size:24px; font-weight:800; color:#2ecc71; margin-top:4px" id="roi-revenue">— zł</div>
              </div>
              <div style="background:rgba(0,212,255,0.1); border-radius:12px; padding:14px; text-align:center">
                <div style="font-size:10px; color:#00d4ff; text-transform:uppercase; letter-spacing:1px">🏦 Oszczędność</div>
                <div style="font-size:24px; font-weight:800; color:#00d4ff; margin-top:4px" id="roi-savings">— zł</div>
                <div style="font-size:9px; color:#94a3b8; margin-top:2px">(autokonsumpcja)</div>
              </div>
              <div style="border-radius:12px; padding:14px; text-align:center; border:2px solid" id="roi-balance-card">
                <div style="font-size:10px; text-transform:uppercase; letter-spacing:1px">📊 Bilans netto</div>
                <div style="font-size:28px; font-weight:900; margin-top:4px" id="roi-balance">— zł</div>
                <div style="font-size:9px; color:#94a3b8; margin-top:2px">(przychód + oszczędność − koszt)</div>
              </div>
            </div>
          </div>

          <!-- ROW 3: Efficiency -->
          <div class="grid-cards gc-2" style="margin-bottom:12px">
            <div class="card">
              <div class="card-title">🎯 Efektywność</div>
              <div class="dr"><span class="lb">Autarkia</span><span class="vl" id="roi-autarky">—%</span></div>
              <div style="margin:6px 0; background:rgba(255,255,255,0.08); border-radius:6px; height:8px; overflow:hidden">
                <div id="roi-autarky-bar" style="height:100%; width:0%; background:#2ecc71; border-radius:6px; transition:width 0.5s"></div>
              </div>
              <div class="dr"><span class="lb">Autokonsumpcja</span><span class="vl" id="roi-selfcons">—%</span></div>
              <div style="margin:6px 0; background:rgba(255,255,255,0.08); border-radius:6px; height:8px; overflow:hidden">
                <div id="roi-selfcons-bar" style="height:100%; width:0%; background:#00d4ff; border-radius:6px; transition:width 0.5s"></div>
              </div>
            </div>
          </div>

          <!-- ROW 3b: ROI — 3 Scenario Comparison -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">🏗️ Zwrot inwestycji (ROI) — Porównanie taryf</div>
            <div style="font-size:10px; color:#94a3b8; margin-bottom:10px">Jak wybór taryfy wpływa na Twój zwrot z inwestycji i zyski w perspektywie 25 lat. Identyczne zużycie i przepływy energii — różnica wynika z cen.</div>
            <div style="display:flex; align-items:center; gap:10px; margin-bottom:14px; padding:10px; background:rgba(255,255,255,0.03); border-radius:8px; border:1px solid rgba(255,255,255,0.06)">
              <span style="font-size:11px; color:#94a3b8; white-space:nowrap">💰 Koszt instalacji</span>
              <input id="roi-invest-input" type="number" value="" placeholder="np. 45000" style="width:100px; background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.15); border-radius:6px; color:#fff; padding:6px 10px; font-size:13px; text-align:right" onchange="this.getRootNode().host._saveRoiInvestment(this.value)">
              <span style="font-size:12px; color:#94a3b8">zł</span>
              <div style="flex:1"></div>
              <span style="font-size:10px; color:#64748b" id="roi-period-label">Baza: —</span>
            </div>
            <div class="g3" id="roi-scenario-cards">
              <div style="text-align:center; padding:20px; color:#64748b; font-size:11px">Podaj koszt instalacji aby zobaczyć porównanie scenariuszy.</div>
            </div>
          </div>

          <!-- ROW 4: Summary table -->
          <div class="card">
            <div class="card-title">📋 Podsumowanie okresów</div>
            <div style="overflow-x:auto">
              <table style="width:100%; border-collapse:collapse; font-size:11px; margin-top:8px">
                <thead>
                  <tr style="border-bottom:1px solid rgba(255,255,255,0.1)">
                    <th style="text-align:left; padding:6px 8px; color:#94a3b8; font-weight:600">Okres</th>
                    <th style="text-align:right; padding:6px 8px; color:#f7b731">PV kWh</th>
                    <th style="text-align:right; padding:6px 8px; color:#e74c3c">Import kWh</th>
                    <th style="text-align:right; padding:6px 8px; color:#2ecc71">Eksport kWh</th>
                    <th style="text-align:right; padding:6px 8px; color:#00d4ff">Bilans zł</th>
                  </tr>
                </thead>
                <tbody>
                  <tr style="border-bottom:1px solid rgba(255,255,255,0.05)">
                    <td style="padding:6px 8px; color:#cbd5e1">📅 Dziś</td>
                    <td style="text-align:right; padding:6px 8px" id="roi-tbl-pv-d">—</td>
                    <td style="text-align:right; padding:6px 8px" id="roi-tbl-imp-d">—</td>
                    <td style="text-align:right; padding:6px 8px" id="roi-tbl-exp-d">—</td>
                    <td style="text-align:right; padding:6px 8px; font-weight:700" id="roi-tbl-bal-d">—</td>
                  </tr>
                  <tr style="border-bottom:1px solid rgba(255,255,255,0.05)">
                    <td style="padding:6px 8px; color:#cbd5e1">📆 Tydzień</td>
                    <td style="text-align:right; padding:6px 8px" id="roi-tbl-pv-w">—</td>
                    <td style="text-align:right; padding:6px 8px" id="roi-tbl-imp-w">—</td>
                    <td style="text-align:right; padding:6px 8px" id="roi-tbl-exp-w">—</td>
                    <td style="text-align:right; padding:6px 8px; font-weight:700" id="roi-tbl-bal-w">—</td>
                  </tr>
                  <tr style="border-bottom:1px solid rgba(255,255,255,0.05)">
                    <td style="padding:6px 8px; color:#cbd5e1">🗓️ Miesiąc</td>
                    <td style="text-align:right; padding:6px 8px" id="roi-tbl-pv-m">—</td>
                    <td style="text-align:right; padding:6px 8px" id="roi-tbl-imp-m">—</td>
                    <td style="text-align:right; padding:6px 8px" id="roi-tbl-exp-m">—</td>
                    <td style="text-align:right; padding:6px 8px; font-weight:700" id="roi-tbl-bal-m">—</td>
                  </tr>
                  <tr>
                    <td style="padding:6px 8px; color:#cbd5e1">📊 Rok</td>
                    <td style="text-align:right; padding:6px 8px" id="roi-tbl-pv-y">—</td>
                    <td style="text-align:right; padding:6px 8px" id="roi-tbl-imp-y">—</td>
                    <td style="text-align:right; padding:6px 8px" id="roi-tbl-exp-y">—</td>
                    <td style="text-align:right; padding:6px 8px; font-weight:700" id="roi-tbl-bal-y">—</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

        </div>

        <!-- ═══════ TAB: WINTER (Zima na plusie) ═══════ -->
        <div class="tab-content" data-tab="winter">

          <!-- Hero: Annual Balance -->
          <div class="card" style="margin-bottom:12px; text-align:center; padding:20px; position:relative; overflow:hidden">
            <div style="position:absolute; top:0; left:0; right:0; bottom:0; opacity:0.03; font-size:120px; display:flex; align-items:center; justify-content:center">❄️</div>
            <div class="card-title" style="position:relative">❄️ Zima na plusie — Bilans roczny</div>
            <div style="font-size:11px; color:#94a3b8; margin-bottom:14px; position:relative">Wprowadź dane zużycia z poprzedniego roku aby precyzyjnie planować zimę.</div>
            <div style="display:grid; grid-template-columns:1fr auto 1fr; gap:16px; align-items:center; position:relative">
              <div>
                <div style="font-size:10px; color:#e74c3c; text-transform:uppercase; letter-spacing:1px">🏠 Roczne zużycie</div>
                <div style="font-size:28px; font-weight:900; color:#e74c3c; margin-top:4px" id="wnt-total-consumption">— kWh</div>
              </div>
              <div style="font-size:32px; font-weight:900" id="wnt-balance-sign">=</div>
              <div>
                <div style="font-size:10px; color:#f7b731; text-transform:uppercase; letter-spacing:1px">☀️ Roczna produkcja PV</div>
                <div style="font-size:28px; font-weight:900; color:#f7b731; margin-top:4px" id="wnt-total-production">— kWh</div>
              </div>
            </div>
            <div style="margin-top:14px; padding:12px; border-radius:12px; position:relative" id="wnt-balance-box">
              <div style="font-size:10px; text-transform:uppercase; letter-spacing:1px" id="wnt-balance-label">Bilans roczny</div>
              <div style="font-size:36px; font-weight:900; margin-top:4px" id="wnt-balance-value">— kWh</div>
              <div style="font-size:11px; margin-top:2px" id="wnt-balance-msg">Wypełnij dane poniżej</div>
            </div>
            <div style="margin-top:8px; padding:10px; border-radius:10px; background:rgba(255,255,255,0.03); position:relative">
              <div style="font-size:10px; text-transform:uppercase; letter-spacing:1px; color:#64748b">Bilans finansowy</div>
              <div style="font-size:28px; font-weight:900; margin-top:4px" id="wnt-fbal-value">— zł</div>
              <div style="font-size:10px; margin-top:2px" id="wnt-fbal-msg">Wybierz scenariusz zarządzania energią</div>
            </div>
          </div>

          <!-- System sizing -->
          <div class="grid-cards gc-2" style="margin-bottom:12px">
            <div class="card">
              <div class="card-title">⚙️ Parametry instalacji</div>
              <div class="settings-field" style="margin-bottom:8px">
                <label style="font-size:10px; color:#64748b; text-transform:uppercase">Moc instalacji PV (kWp)</label>
                <input type="number" id="wnt-pv-kwp" value="" placeholder="np. 10" step="0.1" min="0" max="100"
                  style="width:100%; padding:8px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:8px; color:#fff; font-size:13px"
                  onchange="this.getRootNode().host._recalcWinter()" />
              </div>
              <div class="settings-field">
                <label style="font-size:10px; color:#64748b; text-transform:uppercase">Lokalizacja (region Polski)</label>
                <select id="wnt-region"
                  style="width:100%; padding:8px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:8px; color:#fff; font-size:12px"
                  onchange="this.getRootNode().host._recalcWinter()">
                  <option value="south">Południe (Kraków, Rzeszów) — ~1050 kWh/kWp</option>
                  <option value="center" selected>Centrum (Warszawa, Łódź) — ~1000 kWh/kWp</option>
                  <option value="north">Północ (Gdańsk, Szczecin) — ~950 kWh/kWp</option>
                </select>
              </div>
              <div style="font-size:10px; color:#64748b; margin-top:8px">Szacunkowa produkcja roczna: <span id="wnt-est-yearly" style="color:#f7b731; font-weight:600">— kWh</span></div>
              <div class="settings-field" style="margin-top:10px">
                <label style="font-size:10px; color:#64748b; text-transform:uppercase">Scenariusz zarządzania energią</label>
                <select id="wnt-scenario"
                  style="width:100%; padding:8px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:8px; color:#fff; font-size:12px"
                  onchange="this.getRootNode().host._recalcWinter()">
                  <option value="none">🔴 G11 — Stała cena (1.10 zł/kWh)</option>
                  <option value="basic">🟡 G13 — Strefowa (średnio 0.87 zł/kWh)</option>
                  <option value="optimal" selected>🟢 Dynamiczna RCE — arbitraż cenowy HEMS</option>
                </select>
              </div>
            </div>

            <div class="card">
              <div class="card-title">📊 Status zimowej gotowości</div>
              <div style="text-align:center; padding:10px">
                <div style="font-size:60px; margin-bottom:4px" id="wnt-status-emoji">❓</div>
                <div style="font-size:16px; font-weight:700; color:#fff" id="wnt-status-text">Brak danych</div>
                <div style="font-size:11px; color:#94a3b8; margin-top:4px" id="wnt-status-desc">Wprowadź dane zużycia i parametry instalacji</div>
              </div>
              <div style="margin-top:8px; background:rgba(255,255,255,0.08); border-radius:6px; height:12px; overflow:hidden">
                <div id="wnt-coverage-bar" style="height:100%; width:0%; background:linear-gradient(90deg,#e74c3c,#f7b731,#2ecc71); border-radius:6px; transition:width 0.5s"></div>
              </div>
              <div style="display:flex; justify-content:space-between; font-size:9px; color:#64748b; margin-top:2px">
                <span>0%</span><span>Pokrycie PV</span><span>100%+</span>
              </div>
            </div>
          </div>

          <!-- Monthly table -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">📋 Miesięczne dane zużycia i produkcji</div>
            <div style="font-size:10px; color:#94a3b8; margin-bottom:8px">Wpisz zużycie z rachunków za prąd (kWh/miesiąc). Produkcja PV obliczona automatycznie na podstawie mocy i regionu.</div>
            <div style="overflow-x:auto">
              <table style="width:100%; border-collapse:collapse; font-size:11px">
                <thead>
                  <tr style="border-bottom:1px solid rgba(255,255,255,0.1)">
                    <th style="text-align:left; padding:6px 6px; color:#94a3b8; font-weight:600; width:90px">Miesiąc</th>
                    <th style="text-align:right; padding:6px 6px; color:#e74c3c; width:100px">Zużycie kWh</th>
                    <th style="text-align:right; padding:6px 6px; color:#f7b731; width:90px">PV kWh</th>
                    <th style="text-align:right; padding:6px 6px; color:#00d4ff; width:70px">Bilans</th>
                    <th style="text-align:right; padding:6px 6px; color:#e74c3c; width:70px; font-size:10px">Koszt zł</th>
                    <th style="text-align:right; padding:6px 6px; color:#2ecc71; width:70px; font-size:10px">Przychód zł</th>
                    <th style="text-align:right; padding:6px 6px; color:#f7b731; width:70px; font-size:10px">Bilans zł</th>
                    <th style="padding:6px 6px; width:100px"></th>
                  </tr>
                </thead>
                <tbody id="wnt-table-body">
                </tbody>
                <tfoot>
                  <tr style="border-top:2px solid rgba(255,255,255,0.15)">
                    <td style="padding:8px 6px; font-weight:700; color:#fff">RAZEM</td>
                    <td style="text-align:right; padding:8px 6px; font-weight:700; color:#e74c3c" id="wnt-sum-cons">—</td>
                    <td style="text-align:right; padding:8px 6px; font-weight:700; color:#f7b731" id="wnt-sum-pv">—</td>
                    <td style="text-align:right; padding:8px 6px; font-weight:800; font-size:13px" id="wnt-sum-bal">—</td>
                    <td style="text-align:right; padding:8px 6px; font-weight:600; color:#e74c3c; font-size:10px" id="wnt-sum-cost">—</td>
                    <td style="text-align:right; padding:8px 6px; font-weight:600; color:#2ecc71; font-size:10px" id="wnt-sum-rev">—</td>
                    <td style="text-align:right; padding:8px 6px; font-weight:800; font-size:12px" id="wnt-sum-fbal">—</td>
                    <td></td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </div>

          <!-- Visual bar chart -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">📊 Wykres: Produkcja PV vs Zużycie (miesięcznie)</div>
            <div id="wnt-chart" style="display:flex; align-items:flex-end; gap:4px; height:180px; padding:10px 0"></div>
          </div>

          <!-- Scenario comparison -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">💰 Porównanie scenariuszy zarządzania energią</div>
            <div style="font-size:10px; color:#94a3b8; margin-bottom:8px">Jak wybór strategii zarządzania energią wpływa na Twój bilans finansowy rocznie.</div>
            <div id="wnt-scenario-compare"></div>
          </div>

          <!-- Winter months focus -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">🥶 Miesiące zimowe (X–III) — Focus</div>
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(130px, 1fr)); gap:8px; margin-top:8px" id="wnt-winter-cards">
            </div>
            <div style="margin-top:12px; padding:10px; background:rgba(0,212,255,0.05); border:1px solid rgba(0,212,255,0.15); border-radius:10px">
              <div style="font-size:12px; font-weight:600; color:#00d4ff; margin-bottom:4px">💡 Sugestie na zimę</div>
              <div style="font-size:11px; color:#94a3b8; line-height:1.6" id="wnt-suggestions">
                Wypełnij dane aby zobaczyć spersonalizowane sugestie.
              </div>
            </div>
          </div>

          <button class="save-btn" onclick="this.getRootNode().host._saveWinterData()" style="margin-bottom:10px">💾 Zapisz dane zimowe</button>
          <div id="wnt-save-status" style="font-size:11px; color:#2ecc71; margin-bottom:10px"></div>

        </div>

        <!-- ═══════ TAB: WIND POWER ═══════ -->
        <div class="tab-content" data-tab="wind">

          <!-- Hero: Live Wind Data -->
          <div class="card" style="margin-bottom:12px; position:relative; overflow:hidden">
            <div style="position:absolute; top:0; left:0; right:0; bottom:0; opacity:0.03; font-size:120px; display:flex; align-items:center; justify-content:center">🌬️</div>
            <div class="card-title" style="position:relative">🌬️ Elektrownia Wiatrowa — Dane na żywo</div>
            <div style="font-size:10px; color:#94a3b8; margin-bottom:12px; position:relative">Dane z Ecowitt WH90 • Analiza potencjału wiatrowego Twojej lokalizacji</div>
            <div id="wind-live-data">
              <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px">
                <!-- Wind speed -->
                <div style="background:rgba(0,212,255,0.06); border:1px solid rgba(0,212,255,0.15); border-radius:14px; padding:16px; text-align:center">
                  <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">💨 Prędkość wiatru</div>
                  <div style="font-size:32px; font-weight:900; color:#00d4ff; margin-top:6px"><span id="wind-speed-val">—</span> <span style="font-size:14px; font-weight:400; color:#94a3b8">km/h</span></div>
                  <div style="font-size:12px; color:#64748b; margin-top:2px" id="wind-speed-ms"></div>
                  <div style="font-size:11px; color:#94a3b8; margin-top:4px">Porywy: <span id="wind-gust-val" style="color:#f39c12; font-weight:600">—</span> km/h <span id="wind-gust-ms" style="color:#64748b; font-size:10px"></span></div>
                  <div style="margin-top:8px; padding:6px 12px; border-radius:20px; background:rgba(255,255,255,0.05); display:inline-block">
                    <span id="wind-beaufort-name" style="font-size:12px; font-weight:700">—</span>
                    <span id="wind-beaufort-scale" style="font-size:10px; color:#64748b; margin-left:4px">—</span>
                  </div>
                </div>
                <!-- Wind direction + compass -->
                <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.06); border-radius:14px; padding:16px; text-align:center">
                  <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">🧭 Kierunek wiatru</div>
                  <div style="font-size:18px; font-weight:800; color:#fff; margin-top:6px" id="wind-dir-val">—</div>
                  <div style="margin-top:8px; display:flex; justify-content:center">
                    <svg viewBox="0 0 120 120" width="90" height="90">
                      <circle cx="60" cy="60" r="55" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="1"/>
                      <circle cx="60" cy="60" r="40" fill="none" stroke="rgba(255,255,255,0.04)" stroke-width="0.5"/>
                      <text x="60" y="12" text-anchor="middle" fill="#94a3b8" font-size="9" font-weight="700">N</text>
                      <text x="110" y="63" text-anchor="middle" fill="#64748b" font-size="8">E</text>
                      <text x="60" y="115" text-anchor="middle" fill="#64748b" font-size="8">S</text>
                      <text x="10" y="63" text-anchor="middle" fill="#64748b" font-size="8">W</text>
                      <g id="wind-compass-needle" transform="rotate(0, 60, 60)">
                        <polygon points="60,18 55,60 65,60" fill="#00d4ff" opacity="0.9"/>
                        <polygon points="60,102 55,60 65,60" fill="rgba(255,255,255,0.15)"/>
                      </g>
                      <circle cx="60" cy="60" r="4" fill="#fff"/>
                    </svg>
                  </div>
                </div>
                <!-- Instant power -->
                <div style="background:rgba(46,204,113,0.06); border:1px solid rgba(46,204,113,0.15); border-radius:14px; padding:16px; text-align:center">
                  <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">⚡ Moc chwilowa</div>
                  <div style="font-size:28px; font-weight:900; color:#2ecc71; margin-top:6px" id="wind-instant-power">— W</div>
                  <div style="font-size:11px; margin-top:4px" id="wind-turbine-status" style="color:#64748b">—</div>
                  <div style="margin-top:10px; background:rgba(255,255,255,0.08); border-radius:6px; height:10px; overflow:hidden">
                    <div id="wind-power-bar-fill" style="height:100%; width:0%; border-radius:6px; transition:width 0.5s"></div>
                  </div>
                  <div style="font-size:10px; color:#94a3b8; margin-top:4px">Obciążenie: <span id="wind-power-pct" style="font-weight:600">0%</span></div>
                </div>
              </div>

              <!-- Daily estimation strip -->
              <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:10px">
                <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:12px; text-align:center">
                  <div style="font-size:9px; color:#64748b; text-transform:uppercase">📊 Estymacja dzienna</div>
                  <div style="font-size:22px; font-weight:800; color:#f7b731; margin-top:4px" id="wind-daily-est">— kWh</div>
                </div>
                <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:12px; text-align:center">
                  <div style="font-size:9px; color:#64748b; text-transform:uppercase">💰 Przychód dzienny</div>
                  <div style="font-size:22px; font-weight:800; color:#2ecc71; margin-top:4px" id="wind-daily-revenue">— zł</div>
                </div>
              </div>
            </div>
          </div>

          <!-- Turbine configuration -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">⚙️ Konfiguracja turbiny wiatrowej</div>
            <div style="font-size:10px; color:#94a3b8; margin-bottom:12px">Wprowadź parametry planowanej lub istniejącej turbiny przydomowej.</div>

            <!-- Preset selector -->
            <div style="margin-bottom:14px">
              <div style="font-size:10px; color:#64748b; text-transform:uppercase; margin-bottom:6px; letter-spacing:0.5px">🔧 Wczytaj wariant turbiny</div>
              <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center">
                <button id="wind-preset-small" onclick="this.getRootNode().host._applyWindPreset('small')"
                  style="padding:8px 14px; border-radius:10px; border:1px solid rgba(255,255,255,0.12); background:rgba(255,255,255,0.05); color:#94a3b8; font-size:11px; font-weight:700; cursor:pointer; transition:all 0.2s">
                  🌬️ Mała (1 kW)
                </button>
                <button id="wind-preset-medium" onclick="this.getRootNode().host._applyWindPreset('medium')"
                  style="padding:8px 14px; border-radius:10px; border:1px solid rgba(0,212,255,1); background:rgba(0,212,255,0.2); color:#00d4ff; font-size:11px; font-weight:700; cursor:pointer; transition:all 0.2s">
                  💨 Średnia (3 kW)
                </button>
                <button id="wind-preset-large" onclick="this.getRootNode().host._applyWindPreset('large')"
                  style="padding:8px 14px; border-radius:10px; border:1px solid rgba(255,255,255,0.12); background:rgba(255,255,255,0.05); color:#94a3b8; font-size:11px; font-weight:700; cursor:pointer; transition:all 0.2s">
                  🌪️ Duża (5 kW)
                </button>
                <span id="wind-preset-custom-badge" style="display:none; font-size:10px; color:#f39c12; font-weight:600; padding:4px 10px; background:rgba(243,156,18,0.1); border:1px solid rgba(243,156,18,0.3); border-radius:8px">✏️ Własna konfiguracja</span>
              </div>
            </div>

            <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px">
              <div class="settings-field">
                <label style="font-size:10px; color:#64748b; text-transform:uppercase">Moc nominalna (kW)</label>
                <input type="number" id="wind-turbine-power" step="0.1" min="0.1" max="50" placeholder="3"
                  style="width:100%; padding:8px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:8px; color:#fff; font-size:13px"
                  onchange="this.getRootNode().host._onWindFieldManualChange()" />
              </div>
              <div class="settings-field">
                <label style="font-size:10px; color:#64748b; text-transform:uppercase">Średnica rotora (m)</label>
                <input type="number" id="wind-turbine-diameter" step="0.1" min="0.5" max="20" placeholder="3.2"
                  style="width:100%; padding:8px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:8px; color:#fff; font-size:13px"
                  onchange="this.getRootNode().host._onWindFieldManualChange()" />
              </div>
              <div class="settings-field">
                <label style="font-size:10px; color:#64748b; text-transform:uppercase">Prędkość startu (m/s)</label>
                <input type="number" id="wind-turbine-cutin" step="0.1" min="0.5" max="10" placeholder="3"
                  style="width:100%; padding:8px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:8px; color:#fff; font-size:13px"
                  onchange="this.getRootNode().host._onWindFieldManualChange()" />
              </div>
              <div class="settings-field">
                <label style="font-size:10px; color:#64748b; text-transform:uppercase">Prędkość nominalna (m/s)</label>
                <input type="number" id="wind-turbine-rated" step="0.1" min="3" max="25" placeholder="12"
                  style="width:100%; padding:8px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:8px; color:#fff; font-size:13px"
                  onchange="this.getRootNode().host._onWindFieldManualChange()" />
              </div>
              <div class="settings-field">
                <label style="font-size:10px; color:#64748b; text-transform:uppercase">Koszt inwestycji (zł)</label>
                <input type="number" id="wind-turbine-investment" step="100" min="0" placeholder="25000"
                  style="width:100%; padding:8px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:8px; color:#fff; font-size:13px"
                  onchange="this.getRootNode().host._onWindFieldManualChange()" />
              </div>
              <div class="settings-field">
                <label style="font-size:10px; color:#64748b; text-transform:uppercase">Cena prądu (zł/kWh)</label>
                <input type="number" id="wind-turbine-price" step="0.01" min="0" placeholder="0.87"
                  style="width:100%; padding:8px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:8px; color:#fff; font-size:13px"
                  onchange="this.getRootNode().host._onWindFieldManualChange()" />
              </div>
            </div>
            <div style="margin-top:10px; display:flex; gap:8px; align-items:center">
              <button class="save-btn" onclick="this.getRootNode().host._saveWindData()" style="flex-shrink:0">💾 Zapisz konfigurację</button>
              <span id="wind-save-status" style="font-size:11px; color:#2ecc71"></span>
            </div>
          </div>

          <!-- Profitability analysis -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">💰 Analiza opłacalności — Porównanie scenariuszy wiatru</div>
            <div style="font-size:10px; color:#94a3b8; margin-bottom:12px">Porównanie 4 klas wiatrowości + Twoja rzeczywista lokalizacja na podstawie Ecowitt WH90.</div>
            <div id="wind-profit-cards" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:10px"></div>
          </div>

          <!-- Monthly chart -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">📉 Szacunkowa produkcja miesięczna (kWh)</div>
            <div id="wind-monthly-subtitle" style="font-size:10px; color:#94a3b8; margin-bottom:8px">Ładowanie danych historycznych...</div>
            <div id="wind-monthly-chart" style="display:flex; align-items:flex-end; gap:4px; height:180px; padding:10px 0"></div>
            <div id="wind-chart-legend" style="font-size:9px; color:#64748b; text-align:center; margin-top:6px"></div>
          </div>

          <!-- Recommendation -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">💡 Rekomendacja — czy warto stawiać turbinę?</div>
            <div id="wind-recommendation" style="padding:16px">
              <div style="text-align:center; color:#64748b; font-size:12px">Oczekiwanie na dane wiatru z Ecowitt WH90...</div>
            </div>
          </div>

          <!-- Beaufort scale reference -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">📖 Skala Beauforta — Referencja</div>
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:6px; margin-top:8px">
              <div style="background:rgba(100,116,139,0.1); border-radius:8px; padding:8px; text-align:center"><div style="font-size:16px">🍃</div><div style="font-size:10px; font-weight:700; color:#64748b">0 — Cisza</div><div style="font-size:9px; color:#94a3b8">&lt; 1 km/h (&lt; 0.3 m/s)</div></div>
              <div style="background:rgba(46,204,113,0.08); border-radius:8px; padding:8px; text-align:center"><div style="font-size:16px">🌿</div><div style="font-size:10px; font-weight:700; color:#2ecc71">2-3 — Słaby/Łagodny</div><div style="font-size:9px; color:#94a3b8">6–19 km/h (1.7–5.3 m/s)</div></div>
              <div style="background:rgba(247,183,49,0.08); border-radius:8px; padding:8px; text-align:center"><div style="font-size:16px">🌬️</div><div style="font-size:10px; font-weight:700; color:#f7b731">4-5 — Umiarkowany</div><div style="font-size:9px; color:#94a3b8">20–38 km/h (5.6–10.6 m/s)</div><div style="font-size:8px; color:#00d4ff; margin-top:2px">⚡ START turbiny</div></div>
              <div style="background:rgba(231,76,60,0.08); border-radius:8px; padding:8px; text-align:center"><div style="font-size:16px">💨</div><div style="font-size:10px; font-weight:700; color:#e67e22">6-7 — Silny</div><div style="font-size:9px; color:#94a3b8">39–61 km/h (10.8–16.9 m/s)</div><div style="font-size:8px; color:#2ecc71; margin-top:2px">⚡ Optymalna moc</div></div>
              <div style="background:rgba(192,57,43,0.08); border-radius:8px; padding:8px; text-align:center"><div style="font-size:16px">🌪️</div><div style="font-size:10px; font-weight:700; color:#c0392b">8+ — Sztorm</div><div style="font-size:9px; color:#94a3b8">&gt; 62 km/h (&gt; 17.2 m/s)</div><div style="font-size:8px; color:#e74c3c; margin-top:2px">⛔ STOP bezpieczeństwa</div></div>
            </div>
          </div>

        </div>

        <!-- ═══════ TAB: HISTORY ═══════ -->
        <div class="tab-content" data-tab="history">

          <!-- 🎛️ Control Bar -->
          <div class="hist-control-bar">
            <div style="display:flex; gap:4px">
              <button class="hist-period-btn active" id="hist-period-day" onclick="this.getRootNode().host._switchHistoryPeriod('day')">📅 Dzień</button>
              <button class="hist-period-btn" id="hist-period-week" onclick="this.getRootNode().host._switchHistoryPeriod('week')">📆 Tydzień</button>
              <button class="hist-period-btn" id="hist-period-month" onclick="this.getRootNode().host._switchHistoryPeriod('month')">🗓️ Miesiąc</button>
              <button class="hist-period-btn" id="hist-period-year" onclick="this.getRootNode().host._switchHistoryPeriod('year')">📊 Rok</button>
            </div>
            <div style="display:flex; align-items:center; gap:6px; flex:1; justify-content:center">
              <button class="hist-nav-btn hist-nav-arrow" onclick="this.getRootNode().host._histNavigate(-1)">◀</button>
              <span style="font-size:13px; font-weight:700; color:#e0e6ed; min-width:160px; text-align:center" id="hist-date-label">—</span>
              <button class="hist-nav-btn hist-nav-arrow" onclick="this.getRootNode().host._histNavigate(1)">▶</button>
              <button class="hist-nav-btn hist-nav-arrow" onclick="this.getRootNode().host._histToday()" style="font-size:10px; padding:5px 10px">Dziś</button>
            </div>
            <div style="display:flex; gap:4px; align-items:center">
              <button class="hist-export-btn" onclick="this.getRootNode().host._exportHistoryData('csv')">📄 CSV</button>
              <button class="hist-export-btn" onclick="this.getRootNode().host._exportHistoryData('json')">📋 JSON</button>
              <span style="font-size:10px; color:#2ecc71" id="hist-export-status"></span>
            </div>
          </div>

          <!-- ⚡ Energy KPI -->
          <div class="hist-kpi-grid">
            <div class="hist-kpi" style="border-color: rgba(247,183,49,0.15)">
              <div class="hist-kpi-label">☀️ Produkcja PV</div>
              <div class="hist-kpi-val" style="color:#f7b731" id="hist-pv-val">— kWh</div>
            </div>
            <div class="hist-kpi" style="border-color: rgba(231,76,60,0.15)">
              <div class="hist-kpi-label">🔌 Import z sieci</div>
              <div class="hist-kpi-val" style="color:#e74c3c" id="hist-import-val">— kWh</div>
            </div>
            <div class="hist-kpi" style="border-color: rgba(46,204,113,0.15)">
              <div class="hist-kpi-label">📤 Eksport do sieci</div>
              <div class="hist-kpi-val" style="color:#2ecc71" id="hist-export-val">— kWh</div>
            </div>
            <div class="hist-kpi" style="border-color: rgba(0,212,255,0.15)">
              <div class="hist-kpi-label">🏠 Autokonsumpcja</div>
              <div class="hist-kpi-val" style="color:#00d4ff" id="hist-selfuse-val">— kWh</div>
            </div>
          </div>

          <!-- 💰 Financial KPI -->
          <div class="hist-kpi-grid">
            <div class="hist-kpi" style="border-color: rgba(231,76,60,0.15)">
              <div class="hist-kpi-label">💸 Koszt importu</div>
              <div class="hist-kpi-val" style="color:#e74c3c; font-size:18px" id="hist-cost-val">— zł</div>
            </div>
            <div class="hist-kpi" style="border-color: rgba(46,204,113,0.15)">
              <div class="hist-kpi-label">💰 Przychód eksportu</div>
              <div class="hist-kpi-val" style="color:#2ecc71; font-size:18px" id="hist-rev-val">— zł</div>
            </div>
            <div class="hist-kpi" style="border-color: rgba(0,212,255,0.15)">
              <div class="hist-kpi-label">🛡️ Oszczędności</div>
              <div class="hist-kpi-val" style="color:#00d4ff; font-size:18px" id="hist-sav-val">— zł</div>
            </div>
            <div class="hist-kpi" id="hist-bal-card" style="border-color: rgba(46,204,113,0.3); background: rgba(46,204,113,0.08)">
              <div class="hist-kpi-label">💵 Bilans netto</div>
              <div class="hist-kpi-val" style="font-size:22px" id="hist-bal-val">— zł</div>
            </div>
          </div>

          <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px">
            <!-- ⚡ String Table -->
            <div class="hist-section">
              <div class="hist-section-title">⚡ Produkcja per String</div>
              <table style="width:100%; border-collapse:collapse">
                <thead>
                  <tr style="border-bottom:1px solid rgba(255,255,255,0.1)">
                    <th style="text-align:left; padding:6px 10px; font-size:9px; color:#64748b; text-transform:uppercase">String</th>
                    <th style="text-align:right; padding:6px 10px; font-size:9px; color:#64748b; text-transform:uppercase">Moc</th>
                    <th style="text-align:right; padding:6px 10px; font-size:9px; color:#64748b; text-transform:uppercase">Produkcja</th>
                    <th style="text-align:right; padding:6px 10px; font-size:9px; color:#64748b; text-transform:uppercase">Udział</th>
                  </tr>
                </thead>
                <tbody id="hist-string-tbody"></tbody>
              </table>
            </div>

            <!-- 📈 Efficiency -->
            <div class="hist-section">
              <div class="hist-section-title">📈 Wskaźniki efektywności</div>
              <div class="hist-progress-wrap">
                <span class="hist-progress-label">🛡️ Autarkia</span>
                <div class="hist-progress-bar-bg">
                  <div class="hist-progress-bar-fill" id="hist-autarky-bar" style="width:0%; background:linear-gradient(90deg, #2ecc71, #00d4ff)"></div>
                </div>
                <span class="hist-progress-val" style="color:#2ecc71" id="hist-autarky-val">—%</span>
              </div>
              <div class="hist-progress-wrap">
                <span class="hist-progress-label">♻️ Autokonsumpcja</span>
                <div class="hist-progress-bar-bg">
                  <div class="hist-progress-bar-fill" id="hist-selfcons-bar" style="width:0%; background:linear-gradient(90deg, #f7b731, #e67e22)"></div>
                </div>
                <span class="hist-progress-val" style="color:#f7b731" id="hist-selfcons-val">—%</span>
              </div>
              <div class="hist-progress-wrap">
                <span class="hist-progress-label">☀️ Yield</span>
                <div style="flex:1">
                  <span style="font-size:13px; font-weight:700; color:#00d4ff" id="hist-yield-val">—</span>
                </div>
              </div>
              <div class="hist-progress-wrap">
                <span class="hist-progress-label">🎯 Performance Ratio</span>
                <div class="hist-progress-bar-bg">
                  <div class="hist-progress-bar-fill" id="hist-perf-bar" style="width:0%; background:linear-gradient(90deg, #e74c3c, #f7b731, #2ecc71)"></div>
                </div>
                <span class="hist-progress-val" style="color:#2ecc71" id="hist-perf-val">—</span>
              </div>
            </div>
          </div>

          <!-- 📅 Calendar Heatmap -->
          <div class="hist-section">
            <div class="hist-section-title">
              📅 Kalendarz efektywności
              <span style="font-size:9px; color:#64748b; margin-left:auto">Intensywność koloru = efektywność produkcji</span>
            </div>
            <div id="hist-calendar-grid" style="display:grid; gap:4px; margin-bottom:10px"></div>
            <div style="display:flex; align-items:center; gap:12px; justify-content:center; margin-top:8px">
              <div style="display:flex; align-items:center; gap:4px">
                <div style="width:14px; height:14px; border-radius:3px; background:rgba(231,76,60,0.4)"></div>
                <span style="font-size:9px; color:#94a3b8">Niska</span>
              </div>
              <div style="display:flex; align-items:center; gap:4px">
                <div style="width:14px; height:14px; border-radius:3px; background:rgba(247,183,49,0.4)"></div>
                <span style="font-size:9px; color:#94a3b8">Średnia</span>
              </div>
              <div style="display:flex; align-items:center; gap:4px">
                <div style="width:14px; height:14px; border-radius:3px; background:rgba(46,204,113,0.5)"></div>
                <span style="font-size:9px; color:#94a3b8">Wysoka</span>
              </div>
              <div style="display:flex; align-items:center; gap:4px">
                <div style="width:14px; height:14px; border-radius:3px; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.1)"></div>
                <span style="font-size:9px; color:#94a3b8">Brak danych</span>
              </div>
            </div>
          </div>

          <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px">
            <!-- 📊 Comparison -->
            <div class="hist-section">
              <div class="hist-section-title">📊 Projekcja roczna</div>
              <div style="font-size:10px; color:#64748b; margin-bottom:10px">Ekstrapolacja bieżącego okresu na pełen rok</div>
              <div id="hist-compare-body"></div>
            </div>

            <!-- 🔋 Battery -->
            <div class="hist-section">
              <div class="hist-section-title">🔋 Bateria — podsumowanie</div>
              <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; text-align:center">
                <div>
                  <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">⬆️ Ładowanie</div>
                  <div style="font-size:18px; font-weight:800; color:#2ecc71; margin-top:4px" id="hist-bat-chg">— kWh</div>
                </div>
                <div>
                  <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">⬇️ Rozładowanie</div>
                  <div style="font-size:18px; font-weight:800; color:#e67e22; margin-top:4px" id="hist-bat-dischg">— kWh</div>
                </div>
                <div>
                  <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">🔄 Cykle</div>
                  <div style="font-size:18px; font-weight:800; color:#00d4ff; margin-top:4px" id="hist-bat-cycles">—</div>
                </div>
              </div>
              <div style="margin-top:12px; padding:8px; border-radius:8px; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.04)">
                <div style="font-size:9px; color:#64748b; line-height:1.5">
                  💡 Cykle obliczane jako (ładowanie + rozładowanie) / 2 / pojemność baterii (10.2 kWh).
                  Dane baterii dostępne tylko w widoku dziennym.
                </div>
              </div>
            </div>
          </div>

        </div>

        <!-- ═══════ TAB: AUTOPILOT ═══════ -->
        <div class="tab-content" data-tab="autopilot">

          <!-- ═══ HEADER STRIP ═══ -->
          <div class="ap-header-strip">
            <div class="ap-h-title">🧠 HEMS Autopilot — AI Energy Automation</div>
            <div class="ap-badges">
              <span class="ap-badge active" id="ap-status">● GOTOWY</span>
              <span class="ap-badge provider" id="ap-provider-badge">—</span>
              <span class="ap-badge tier" id="ap-tier-badge">PRO</span>
              <button id="ap-deactivate-btn"
                style="padding:4px 12px; border-radius:8px; background:rgba(231,76,60,0.15); border:1px solid rgba(231,76,60,0.3); color:#e74c3c; font-size:10px; font-weight:700; cursor:pointer; display:none; transition:all 0.3s"
                onclick="this.getRootNode().host._deactivateAutopilot()">⏹ WYŁĄCZ AUTOPILOT</button>
            </div>
          </div>

          <!-- ═══ LIVE STRATEGY DASHBOARD (above presets) ═══ -->
          <div class="card" style="margin-bottom:14px">
            <div class="card-title">⚡ Live — Strategia w akcji</div>

            <!-- Current tick status bar -->
            <div id="ap-live-status" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(95px, 1fr)); gap:6px; margin-bottom:10px">
              <div style="background:rgba(255,255,255,0.04); border-radius:6px; padding:8px 10px; text-align:center">
                <div style="font-size:10px; color:#64748b; text-transform:uppercase">Strategia</div>
                <div id="ap-live-strategy" style="font-size:13px; font-weight:600; color:#f8fafc">—</div>
              </div>
              <div style="background:rgba(255,255,255,0.04); border-radius:6px; padding:8px 10px; text-align:center">
                <div style="font-size:10px; color:#64748b; text-transform:uppercase">Strefa G13</div>
                <div id="ap-live-zone" style="font-size:13px; font-weight:600; color:#f8fafc">—</div>
              </div>
              <div style="background:rgba(255,255,255,0.04); border-radius:6px; padding:8px 10px; text-align:center">
                <div style="font-size:10px; color:#64748b; text-transform:uppercase">SOC</div>
                <div id="ap-live-soc" style="font-size:13px; font-weight:600; color:#2ecc71">—</div>
              </div>
              <div style="background:rgba(255,255,255,0.04); border-radius:6px; padding:8px 10px; text-align:center">
                <div style="font-size:10px; color:#64748b; text-transform:uppercase">PV</div>
                <div id="ap-live-pv" style="font-size:13px; font-weight:600; color:#f7b731">—</div>
              </div>
              <div style="background:rgba(255,255,255,0.04); border-radius:6px; padding:8px 10px; text-align:center">
                <div style="font-size:10px; color:#64748b; text-transform:uppercase">Zużycie</div>
                <div id="ap-live-load" style="font-size:13px; font-weight:600; color:#e74c3c">—</div>
              </div>
              <div style="background:rgba(255,255,255,0.04); border-radius:6px; padding:8px 10px; text-align:center">
                <div style="font-size:10px; color:#64748b; text-transform:uppercase">Nadwyżka</div>
                <div id="ap-live-surplus" style="font-size:13px; font-weight:600; color:#2ecc71">—</div>
              </div>
            </div>

            <!-- Current actions from this tick -->
            <div style="margin-bottom:8px">
              <div style="font-size:10px; color:#64748b; text-transform:uppercase; margin-bottom:4px; letter-spacing:0.5px">🔄 Aktywne akcje (ostatni tick)</div>
              <div id="ap-live-actions" style="font-size:12px; color:#94a3b8; min-height:24px; padding:6px 8px; background:rgba(255,255,255,0.02); border-radius:6px; border-left:3px solid #334155">
                <span style="color:#64748b">Oczekiwanie na dane...</span>
              </div>
            </div>

            <!-- AI Reasoning (visible only for AI strategy) -->
            <div id="ap-ai-reasoning-wrap" style="display:none; margin-bottom:8px">
              <div style="font-size:10px; color:#64748b; text-transform:uppercase; margin-bottom:4px; letter-spacing:0.5px">🧠 AI Controller — Rozumowanie</div>
              <div id="ap-ai-reasoning" style="font-size:12px; color:#a78bfa; min-height:24px; padding:6px 8px; background:rgba(124,58,237,0.06); border-radius:6px; border-left:3px solid #7c3aed">
              </div>
            </div>

            <!-- Decision log feed -->
            <div>
              <div style="font-size:10px; color:#64748b; text-transform:uppercase; margin-bottom:4px; letter-spacing:0.5px">📋 Historia decyzji</div>
              <div id="ap-activity-log" style="font-size:12px; color:#94a3b8; max-height:200px; overflow-y:auto">
                <div style="color:#64748b; text-align:center; padding:8px">Brak aktywności</div>
              </div>
            </div>
          </div>

          <!-- ═══ STRATEGY PRESET SELECTOR (compact) ═══ -->
          <div class="card" style="margin-bottom:14px">
            <div class="card-title" style="display:flex; justify-content:space-between; align-items:center">
              <span>📋 Preset strategii</span>
              <div style="display:flex; align-items:center; gap:8px">
                <span style="font-size:11px; color:#64748b">Wybierz preset → aktywuje zestaw akcji</span>
                <button onclick="this.getRootNode().host._showStrategyHelp()" style="background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.12); border-radius:50%; width:24px; height:24px; color:#94a3b8; font-size:13px; cursor:pointer; display:flex; align-items:center; justify-content:center; transition:all 0.2s" title="Jak działają strategie?">❓</button>
              </div>
            </div>
            <div style="display:flex; flex-wrap:wrap; gap:6px" id="ap-strategy-presets"></div>
          </div>

          <!-- ═══ ACTION SECTIONS (W0-W5) — rendered dynamically ═══ -->
          <div id="ap-action-sections"></div>

          <!-- ═══ AI PROVIDER SELECTOR ═══ -->
          <div class="card" style="margin-bottom:14px">
            <div class="card-title">🤖 Dostawca AI dla Autopilota</div>
            <div style="display:grid; grid-template-columns:1fr 1fr auto; gap:10px; align-items:end">
              <div>
                <div style="font-size:10px; color:#64748b; margin-bottom:4px">Dostawca</div>
                <select id="ap-provider-select" style="width:100%; padding:8px 12px; border-radius:10px; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.1); color:#fff; font-size:12px" onchange="this.getRootNode().host._onAutopilotProviderChange()">
                  <option value="gemini">Google Gemini</option>
                  <option value="anthropic">Anthropic Claude</option>
                </select>
              </div>
              <div>
                <div style="font-size:10px; color:#64748b; margin-bottom:4px">Model</div>
                <select id="ap-model-select" style="width:100%; padding:8px 12px; border-radius:10px; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.1); color:#fff; font-size:12px">
                  <option value="gemini-2.5-flash">Gemini 2.5 Flash</option>
                  <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
                  <option value="gemini-3-flash-preview">Gemini 3 Flash (Preview)</option>
                </select>
              </div>
              <button class="action-btn" onclick="this.getRootNode().host._runAutopilotEstimation()" style="padding:8px 18px; font-size:12px; height:38px; background:linear-gradient(135deg, #7c3aed, #00d4ff); border:none; font-weight:700">
                🚀 Uruchom estymację
              </button>
            </div>
          </div>

          <!-- ═══ CONTEXT: WEATHER + TARIFF ═══ -->
          <div class="card" style="margin-bottom:14px">
            <div class="card-title">🌤️ Kontekst: Pogoda + Taryfa</div>
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(120px, 1fr)); gap:8px" id="ap-context-grid">
              <div style="padding:8px; border-radius:10px; background:rgba(247,183,49,0.08); text-align:center">
                <div style="font-size:9px; color:#64748b">☀️ Prognoza PV</div>
                <div style="font-size:16px; font-weight:700; color:#f7b731" id="ap-ctx-pv">— kWh</div>
              </div>
              <div style="padding:8px; border-radius:10px; background:rgba(0,212,255,0.08); text-align:center">
                <div style="font-size:9px; color:#64748b">🔋 SOC</div>
                <div style="font-size:16px; font-weight:700; color:#00d4ff" id="ap-ctx-soc">—%</div>
              </div>
              <div style="padding:8px; border-radius:10px; background:rgba(46,204,113,0.08); text-align:center">
                <div style="font-size:9px; color:#64748b">💰 RCE</div>
                <div style="font-size:16px; font-weight:700; color:#2ecc71" id="ap-ctx-rce">— zł</div>
              </div>
              <div style="padding:8px; border-radius:10px; background:rgba(231,76,60,0.08); text-align:center">
                <div style="font-size:9px; color:#64748b" id="ap-ctx-g13-label">⏰ Strefa G13</div>
                <div style="font-size:16px; font-weight:700; color:#e74c3c" id="ap-ctx-g13">—</div>
              </div>
              <div style="padding:8px; border-radius:10px; background:rgba(149,165,166,0.08); text-align:center">
                <div style="font-size:9px; color:#64748b">🌡️ Temp.</div>
                <div style="font-size:16px; font-weight:700; color:#95a5a6" id="ap-ctx-temp">—°C</div>
              </div>
              <div style="padding:8px; border-radius:10px; background:rgba(149,165,166,0.08); text-align:center">
                <div style="font-size:9px; color:#64748b">☁️ Chmury</div>
                <div style="font-size:16px; font-weight:700; color:#95a5a6" id="ap-ctx-clouds">—%</div>
              </div>
            </div>
          </div>

          <!-- ═══ 24H TIMELINE ═══ -->
          <div class="card" style="margin-bottom:14px">
            <div class="card-title">🕐 Plan 24h — Timeline</div>
            <div class="ap-timeline" id="ap-timeline">
              <div style="width:100%; display:flex; align-items:center; justify-content:center; color:#64748b; font-size:11px">Kliknij "Uruchom estymację" aby wygenerować plan</div>
            </div>
            <div style="display:flex; justify-content:space-between; margin-top:6px; font-size:8px; color:#475569">
              <span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>23:00</span>
            </div>
            <div style="display:flex; gap:12px; margin-top:8px; font-size:9px">
              <span style="color:#2ecc71">■ Ładuj</span>
              <span style="color:#e74c3c">■ Rozładuj</span>
              <span style="color:#f7b731">■ Sprzedaj</span>
              <span style="color:#64748b">■ Trzymaj</span>
            </div>
          </div>

          <!-- ═══ ESTIMATION RESULTS ═══ -->
          <div class="card" style="margin-bottom:14px">
            <div class="card-title">💰 Estymacja wyników</div>
            <div class="ap-estimation" id="ap-estimation">
              <div class="ap-est-card" style="background:rgba(46,204,113,0.06)">
                <div class="ap-est-label">💵 Oszczędności netto</div>
                <div class="ap-est-val" style="color:#2ecc71" id="ap-est-net">— zł</div>
              </div>
              <div class="ap-est-card" style="background:rgba(0,212,255,0.06)">
                <div class="ap-est-label">🏠 Autokonsumpcja</div>
                <div class="ap-est-val" style="color:#00d4ff" id="ap-est-selfcons">— kWh</div>
              </div>
              <div class="ap-est-card" style="background:rgba(231,76,60,0.06)">
                <div class="ap-est-label">↓ Import</div>
                <div class="ap-est-val" style="color:#e74c3c" id="ap-est-import">— kWh</div>
              </div>
              <div class="ap-est-card" style="background:rgba(46,204,113,0.06)">
                <div class="ap-est-label">↑ Eksport</div>
                <div class="ap-est-val" style="color:#2ecc71" id="ap-est-export">— kWh</div>
              </div>
              <div class="ap-est-card" style="background:rgba(231,76,60,0.06)">
                <div class="ap-est-label">💸 Koszt importu</div>
                <div class="ap-est-val" style="color:#e74c3c" id="ap-est-cost">— zł</div>
              </div>
              <div class="ap-est-card" style="background:rgba(124,58,237,0.06)">
                <div class="ap-est-label">📊 vs Brak zarządzania</div>
                <div class="ap-est-val" style="color:#7c3aed" id="ap-est-vs">— zł</div>
              </div>
            </div>
          </div>

          <!-- ═══ AI ANALYSIS ═══ -->
          <div class="card" style="margin-bottom:14px">
            <div class="card-title">🧠 Analiza AI</div>
            <div class="ap-ai-analysis" id="ap-ai-analysis">
              <div style="color:#64748b; text-align:center; padding:20px">Uruchom estymację, aby AI przeanalizowało strategię i zaproponowało optymalizacje.</div>
            </div>
          </div>



        </div>

        <!-- ═══════ TAB: SETTINGS ═══════ -->
        <div class="tab-content" data-tab="settings">
          <div class="grid-cards gc-2">

            <!-- 🔑 API Keys -->
            <div class="card" style="grid-column: 1 / -1">
              <div class="card-title">🤖 Ustawienia AI — AI Advisor</div>
              <div style="font-size:11px; color:#94a3b8; margin-bottom:10px">Klucze API zarządzaj w: <strong>Ustawienia → Integracje → Smarting HOME → ⚙️ → 🔑 API Keys</strong></div>
              <div style="display:flex; gap:16px; flex-wrap:wrap; margin-bottom:10px">
                <div style="flex:1; min-width:200px">
                  <div style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px">Google Gemini</div>
                  <div style="display:flex; align-items:center; gap:8px">
                    <div class="key-status" id="key-status-gemini" style="flex:1">— Brak klucza</div>
                    <button class="test-btn" id="test-btn-gemini" onclick="this.getRootNode().host._testApiKey('gemini')">🧪 Testuj</button>
                  </div>
                </div>
                <div style="flex:1; min-width:200px">
                  <div style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px">Anthropic Claude</div>
                  <div style="display:flex; align-items:center; gap:8px">
                    <div class="key-status" id="key-status-anthropic" style="flex:1">— Brak klucza</div>
                    <button class="test-btn" id="test-btn-anthropic" onclick="this.getRootNode().host._testApiKey('anthropic')">🧪 Testuj</button>
                  </div>
                </div>
              </div>
              <div style="display:flex; gap:16px; flex-wrap:wrap; margin-top:14px">
                <div class="settings-field" style="flex:1; min-width:200px">
                  <label style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">🤖 Model Gemini</label>
                  <select id="sel-gemini-model" style="width:100%; padding:8px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:8px; color:#fff; font-size:12px">
                    <option value="gemini-2.5-flash">Gemini 2.5 Flash (szybki, domyślny)</option>
                    <option value="gemini-2.5-pro">Gemini 2.5 Pro (zaawansowany)</option>
                    <option value="gemini-3-flash-preview">Gemini 3 Flash (Preview)</option>
                  </select>
                </div>
                <div class="settings-field" style="flex:1; min-width:200px">
                  <label style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">🤖 Model Claude</label>
                  <select id="sel-anthropic-model" style="width:100%; padding:8px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:8px; color:#fff; font-size:12px">
                    <option value="claude-sonnet-4-6">Claude Sonnet 4.6 (szybki)</option>
                    <option value="claude-opus-4-6">Claude Opus 4.6 (najpotężniejszy)</option>
                    <option value="claude-3-5-haiku">Claude Haiku 3.5 (najtańszy)</option>
                  </select>
                </div>
              </div>
              <div style="display:flex; gap:16px; flex-wrap:wrap; margin-top:10px; align-items:flex-end">
                <div class="settings-field" style="flex:1; min-width:200px">
                  <label style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">⭐ Domyślny dostawca AI</label>
                  <select id="sel-default-provider" style="width:100%; padding:8px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:8px; color:#fff; font-size:12px">
                    <option value="gemini">Google Gemini</option>
                    <option value="anthropic">Anthropic Claude</option>
                  </select>
                </div>
                <div style="flex:1; font-size:10px; color:#64748b; padding:8px 0">
                  Dostawca używany przez AI Cron i zapytania HEMS
                </div>
              </div>
              <button class="save-btn" onclick="this.getRootNode().host._saveApiKeys()">💾 Zapisz ustawienia AI</button>
              <div id="v-save-status" style="font-size:11px; color:#2ecc71; margin-top:8px"></div>
            </div>

            <!-- 🤖 AI Cron Settings -->
            <div class="card" style="grid-column: 1 / -1">
              <div class="card-title">🤖 AI Cron — Automatyczne porady</div>
              <div style="font-size:11px; color:#94a3b8; margin-bottom:12px">Ustaw harmonogram automatycznych analiz AI. Wymagane skonfigurowane klucze API powyżej.</div>
              <div style="display:flex; flex-direction:column; gap:10px">
                <!-- HEMS Optimization -->
                <div style="display:flex; align-items:center; gap:10px; padding:8px 12px; background:rgba(255,255,255,0.03); border-radius:8px; flex-wrap:wrap">
                  <input type="checkbox" id="chk-cron-hems" checked style="accent-color:#f7b731; width:16px; height:16px" />
                  <div style="flex:1; min-width:160px">
                    <div style="font-size:12px; font-weight:600; color:#fff">💡 HEMS Optymalizacja</div>
                    <div style="font-size:10px; color:#64748b">Porady optymalizacji zużycia i zarządzania baterią</div>
                  </div>
                  <select id="sel-cron-hems" style="padding:6px 10px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:6px; color:#fff; font-size:11px">
                    <option value="15">co 15 min</option>
                    <option value="30" selected>co 30 min</option>
                    <option value="60">co 1 godz</option>
                    <option value="120">co 2 godz</option>
                  </select>
                  <div style="font-size:9px; color:#64748b; min-width:100px" id="cron-status-hems">—</div>
                </div>
                <!-- Daily Report -->
                <div style="display:flex; align-items:center; gap:10px; padding:8px 12px; background:rgba(255,255,255,0.03); border-radius:8px; flex-wrap:wrap">
                  <input type="checkbox" id="chk-cron-report" checked style="accent-color:#2ecc71; width:16px; height:16px" />
                  <div style="flex:1; min-width:160px">
                    <div style="font-size:12px; font-weight:600; color:#fff">📊 Raport dzienny</div>
                    <div style="font-size:10px; color:#64748b">Podsumowanie produkcji, zużycia i oszczędności</div>
                  </div>
                  <select id="sel-cron-report" style="padding:6px 10px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:6px; color:#fff; font-size:11px">
                    <option value="60">co 1 godz</option>
                    <option value="180">co 3 godz</option>
                    <option value="360" selected>co 6 godz</option>
                    <option value="720">co 12 godz</option>
                  </select>
                  <div style="font-size:9px; color:#64748b; min-width:100px" id="cron-status-report">—</div>
                </div>
                <!-- Anomaly Detection -->
                <div style="display:flex; align-items:center; gap:10px; padding:8px 12px; background:rgba(255,255,255,0.03); border-radius:8px; flex-wrap:wrap">
                  <input type="checkbox" id="chk-cron-anomaly" checked style="accent-color:#e74c3c; width:16px; height:16px" />
                  <div style="flex:1; min-width:160px">
                    <div style="font-size:12px; font-weight:600; color:#fff">🔍 Wykrywanie anomalii</div>
                    <div style="font-size:10px; color:#64748b">Analiza nieprawidłowości w systemie energetycznym</div>
                  </div>
                  <select id="sel-cron-anomaly" style="padding:6px 10px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:6px; color:#fff; font-size:11px">
                    <option value="30">co 30 min</option>
                    <option value="60" selected>co 1 godz</option>
                    <option value="120">co 2 godz</option>
                    <option value="240">co 4 godz</option>
                  </select>
                  <div style="font-size:9px; color:#64748b; min-width:100px" id="cron-status-anomaly">—</div>
                </div>
                <!-- Autopilot Controller -->
                <div style="display:flex; align-items:center; gap:10px; padding:8px 12px; background:rgba(255,255,255,0.03); border-radius:8px; flex-wrap:wrap">
                  <input type="checkbox" id="chk-cron-autopilot" checked style="accent-color:#9b59b6; width:16px; height:16px" />
                  <div style="flex:1; min-width:160px">
                    <div style="font-size:12px; font-weight:600; color:#fff">🧠 Autopilot Controller</div>
                    <div style="font-size:10px; color:#64748b">Log wykonanych komend AI na falowniku</div>
                  </div>
                  <select id="sel-cron-autopilot" style="padding:6px 10px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:6px; color:#fff; font-size:11px">
                    <option value="5">co 5 min</option>
                    <option value="10" selected>co 10 min</option>
                    <option value="15">co 15 min</option>
                    <option value="30">co 30 min</option>
                  </select>
                  <div style="font-size:9px; color:#64748b; min-width:100px" id="cron-status-autopilot">—</div>
                </div>
              </div>
              <button class="save-btn" style="margin-top:12px" onclick="this.getRootNode().host._saveCronSettings()">💾 Zapisz harmonogram AI</button>
              <div id="v-cron-save-status" style="font-size:11px; color:#2ecc71; margin-top:6px"></div>
            </div>

            <!-- 📝 AI Logs -->
            <div class="card" style="grid-column: 1 / -1">
              <div class="card-title">📝 Logi AI</div>
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px">
                <div style="font-size:11px; color:#94a3b8">Historia wywołań AI Advisor — status, czas i rozmiar odpowiedzi.</div>
                <div style="display:flex; align-items:center; gap:8px">
                  <span style="font-size:10px; color:#64748b" id="ai-logs-count">—</span>
                  <button style="padding:4px 10px; background:rgba(231,76,60,0.1); border:1px solid rgba(231,76,60,0.2); border-radius:6px; color:#e74c3c; font-size:10px; font-weight:600; cursor:pointer" onclick="this.getRootNode().host._clearAILogs()">🗑️ Wyczyść</button>
                </div>
              </div>
              <div id="ai-logs-body" style="max-height:300px; overflow-y:auto; border-radius:8px; background:rgba(0,0,0,0.2)">
                <div style="text-align:center; color:#64748b; padding:20px; font-size:12px">Ładowanie logów...</div>
              </div>
              <div id="ai-logs-status" style="font-size:11px; color:#2ecc71; margin-top:6px"></div>
            </div>

            <!-- 🌦️ Ecowitt Integration -->
            <div class="card" style="grid-column: 1 / -1">
              <div class="card-title">🌦️ Integracja Ecowitt — Lokalna stacja pogodowa</div>
              <div style="font-size:11px; color:#94a3b8; margin-bottom:10px">Włącz integrację z Ecowitt aby użyć lokalnych sensorów pogodowych WH90 do wyświetlania natychmiastowej pogody w panelu.</div>
              <div style="display:flex; align-items:center; gap:10px; padding:10px 12px; background:rgba(0,212,255,0.04); border-radius:8px; border:1px solid rgba(0,212,255,0.1)">
                <input type="checkbox" id="chk-ecowitt-enabled" style="accent-color:#00d4ff; width:18px; height:18px" />
                <div style="flex:1">
                  <div style="font-size:13px; font-weight:700; color:#fff">🌦️ Włącz Ecowitt</div>
                  <div style="font-size:10px; color:#64748b">Automatycznie mapuj sensory Ecowitt WH90 i wyświetlaj dane pogodowe na żywo</div>
                </div>
              </div>
              <div id="ecowitt-detect-status" style="font-size:11px; color:#64748b; margin-top:8px; padding:6px 12px">— Kliknij Zapisz aby wykryć sensory</div>
              <button class="save-btn" style="margin-top:10px" onclick="this.getRootNode().host._saveEcowittSettings()">💾 Zapisz ustawienia Ecowitt</button>
              <div id="v-ecowitt-save-status" style="font-size:11px; color:#2ecc71; margin-top:6px"></div>
            </div>

            <!-- ⚡ Tariff Plan -->
            <div class="card" style="grid-column: 1 / -1">
              <div class="card-title">⚡ Taryfa energetyczna</div>
              <div style="font-size:11px; color:#94a3b8; margin-bottom:10px">Wybierz taryfę aby panel dynamicznie dostosował harmonogram stref cenowych, wskaźniki i rekomendacje.</div>
              <div class="settings-field">
                <label style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">Plan taryfowy</label>
                <select id="sel-tariff-plan" style="width:100%; max-width:400px; padding:10px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:8px; color:#fff; font-size:13px" onchange="this.getRootNode().host._saveTariffPlan()">
                  <option value="G13">G13 — trzystrefowa (przedpołudniowa / popołudniowa / off-peak + weekendy)</option>
                  <option value="G12w">G12w — dwustrefowa + weekendy (13-15 + 22-06 + weekendy taniej)</option>
                  <option value="G12">G12 — dwustrefowa (13-15 + 22-06 taniej)</option>
                  <option value="G11">G11 — jednostrefowa (stała cena cały czas)</option>
                  <option value="Dynamic">Dynamiczna — cena godzinowa ENTSO-E (Tauron Prąd z Ceną Dynamiczną)</option>
                </select>
              </div>
            </div>

            <!-- 🖼️ Inverter Image Upload -->
            <div class="card" style="grid-column: 1 / -1">
              <div class="card-title">🖼️ Zdjęcie falownika</div>
              <div style="font-size:11px; color:#94a3b8; margin-bottom:10px">Wgraj zdjęcie falownika, które będzie wyświetlane w panelu Przegląd na środku diagramu przepływów.</div>

              <!-- Preview -->
              <div style="text-align:center; margin-bottom:12px">
                <img id="upload-preview" style="display:none; max-width:160px; max-height:120px; border-radius:10px; border:1px solid rgba(255,255,255,0.1); background:repeating-conic-gradient(#1e293b 0% 25%, #0f172a 0% 50%) 50% / 16px 16px" />
                <div id="upload-size-info" style="font-size:10px; color:#64748b; margin-top:4px"></div>
              </div>

              <!-- Upload zone -->
              <div class="upload-zone" onclick="this.parentElement.querySelector('input[type=file]').click()" style="cursor:pointer">
                <div style="font-size:32px; margin-bottom:6px">📂</div>
                <div style="font-size:12px; color:#a0aec0">Kliknij aby wybrać plik PNG/JPG</div>
              </div>
              <input type="file" accept="image/png,image/jpeg" style="display:none" onchange="this.getRootNode().host._uploadInverterImage(this.files[0])" />
              <div id="v-upload-status" style="font-size:11px; color:#2ecc71; margin-top:8px"></div>

              <!-- Recommendations -->
              <div style="margin-top:12px; padding:10px; border-radius:8px; background:rgba(0,212,255,0.04); border:1px solid rgba(0,212,255,0.1)">
                <div style="font-size:11px; font-weight:700; color:#00d4ff; margin-bottom:6px">💡 Zalecenia</div>
                <div style="font-size:10px; color:#94a3b8; line-height:1.6">
                  • <strong>Domyślne zdjęcia:</strong> GoodWe, Deye i Growatt wczytywane automatycznie z smartinghome.pl<br>
                  • <strong>Format:</strong> PNG z przezroczystym tłem (transparent) — najlepszy efekt<br>
                  • <strong>Rozmiar:</strong> 500–800px szerokości, proporcjonalne<br>
                  • <strong>Nazwa pliku:</strong> <code style="color:#f39c12">goodwe.png</code> lub <code style="color:#f39c12">deye.png</code> lub <code style="color:#f39c12">growatt.png</code> (nadpisuje domyślne) lub <code style="color:#f39c12">inverter.png</code><br>
                  • <strong>Ścieżka:</strong> <code style="color:#00d4ff">/config/www/smartinghome/</code>
                </div>
              </div>
            </div>

            <!-- 🏠 House Image Upload -->
            <div class="card" style="grid-column: 1 / -1">
              <div class="card-title">🏠 Zdjęcie domu</div>
              <div style="font-size:11px; color:#94a3b8; margin-bottom:10px">Wgraj zdjęcie swojego domu, które pojawi się w sekcji Zużycie na diagramie przepływów.</div>

              <div style="text-align:center; margin-bottom:12px">
                <img id="home-upload-preview" style="display:none; max-width:160px; max-height:100px; border-radius:10px; border:1px solid rgba(255,255,255,0.1); background:repeating-conic-gradient(#1e293b 0% 25%, #0f172a 0% 50%) 50% / 16px 16px" />
              </div>

              <div class="upload-zone" onclick="this.parentElement.querySelector('input[type=file].home-file').click()" style="cursor:pointer">
                <div style="font-size:32px; margin-bottom:6px">🏡</div>
                <div style="font-size:12px; color:#a0aec0">Kliknij aby wybrać zdjęcie domu PNG/JPG</div>
              </div>
              <input type="file" accept="image/png,image/jpeg" class="home-file" style="display:none" onchange="this.getRootNode().host._uploadHomeImage(this.files[0])" />
              <div id="v-home-upload-status" style="font-size:11px; color:#2ecc71; margin-top:8px"></div>

              <div style="margin-top:12px; padding:10px; border-radius:8px; background:rgba(46,204,113,0.04); border:1px solid rgba(46,204,113,0.1)">
                <div style="font-size:11px; font-weight:700; color:#2ecc71; margin-bottom:6px">💡 Zalecenia</div>
                <div style="font-size:10px; color:#94a3b8; line-height:1.6">
                  • <strong>Domyślna grafika:</strong> wczytywana automatycznie z smartinghome.pl<br>
                  • <strong>Format:</strong> PNG z przezroczystym tłem — najlepszy efekt<br>
                  • <strong>Rozmiar:</strong> 400–600px szerokości<br>
                  • <strong>Nazwa pliku:</strong> <code style="color:#f39c12">home.png</code><br>
                  • <strong>Ścieżka:</strong> <code style="color:#00d4ff">/config/www/smartinghome/</code>
                </div>
              </div>
            </div>

            <!-- 📊 Sub-Meters Configuration -->
            <div class="card" style="grid-column: 1 / -1">
              <div class="card-title">📊 Podliczniki energii — monitorowanie zużycia</div>
              <div style="font-size:11px; color:#94a3b8; margin-bottom:10px">Dodaj podliczniki lub urządzenia monitorujące zużycie energii. Wyświetlą się na zakładce Przegląd jako kolumny z aktualną mocą i zużyciem.</div>
              <div style="display:flex; align-items:center; gap:10px; padding:10px 12px; background:rgba(0,212,255,0.04); border-radius:8px; border:1px solid rgba(0,212,255,0.1); margin-bottom:6px">
                <input type="checkbox" id="chk-submeters-enabled" style="accent-color:#00d4ff; width:18px; height:18px" />
                <div style="flex:1">
                  <div style="font-size:13px; font-weight:700; color:#fff">📊 Pokaż podliczniki na Przeglądzie</div>
                  <div style="font-size:10px; color:#64748b">Wyświetlaj kolumnowe karty zużycia pod diagramem przepływów</div>
                </div>
              </div>
              <div style="display:flex; align-items:center; gap:10px; padding:10px 12px; background:rgba(46,204,113,0.04); border-radius:8px; border:1px solid rgba(46,204,113,0.1); margin-bottom:12px">
                <input type="checkbox" id="chk-submeters-in-card" style="accent-color:#2ecc71; width:18px; height:18px" />
                <div style="flex:1">
                  <div style="font-size:13px; font-weight:700; color:#fff">🏠 Pokaż w karcie Zużycie</div>
                  <div style="font-size:10px; color:#64748b">Wyświetlaj podliczniki wewnątrz kontenera 🏠 Zużycie na diagramie</div>
                </div>
              </div>
              <div id="submeters-settings-list"></div>
              <div style="display:flex; gap:8px; margin-top:10px">
                <button style="padding:8px 16px; border:1px dashed rgba(0,212,255,0.3); background:rgba(0,212,255,0.04); color:#00d4ff; font-size:12px; font-weight:600; cursor:pointer; border-radius:8px; transition:all 0.2s; flex:1" onclick="this.getRootNode().host._addSubMeter()">＋ Dodaj podlicznik</button>
              </div>
              <button class="save-btn" style="margin-top:12px" onclick="this.getRootNode().host._saveSubMetersSettings()">💾 Zapisz podliczniki</button>
              <div id="v-submeters-save-status" style="font-size:11px; color:#2ecc71; margin-top:6px"></div>
            </div>

            <!-- 🔑 License Status -->
            <div class="card" style="grid-column: 1 / -1" id="settings-license-box">
              <!-- FREE: Upgrade Prompt -->
              <div id="settings-upgrade-box">
                <div class="upgrade-box">
                  <div style="font-size:15px; font-weight:700; color:#f7b731">⭐ Odblokuj pełną moc Smarting HOME</div>
                  <div style="font-size:12px; color:#a0aec0; margin-top:6px; line-height:1.5">
                    Wersja <strong style="color:#00d4ff">PRO</strong> oferuje:<br>
                    🧠 Pełny silnik HEMS z 3-warstwową optymalizacją<br>
                    📈 Arbitraż nocny (zysk ~177 PLN/mies.)<br>
                    🛡️ Kaskada napięciowa i ochrona SOC<br>
                    📊 7 gotowych blueprintów automatyzacji
                  </div>
                  <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:10px">
                    <a href="https://smartinghome.pl/buy" target="_blank" style="display:inline-block; padding:8px 20px; border-radius:8px; background:linear-gradient(135deg,#f7b731,#e67e22); color:#0a1628; font-weight:700; font-size:12px; text-decoration:none; transition:all 0.2s">🛒 Kup licencję PRO</a>
                    <div style="font-size:10px; color:#64748b; align-self:center">Masz już klucz? Wejdź w: Ustawienia → Urządzenia → Smarting HOME → Konfiguruj → ⚙️ Settings & Upgrade</div>
                  </div>
                </div>
              </div>
              <!-- PRO: License Active -->
              <div id="settings-pro-box" style="display:none">
                <div style="display:flex; align-items:center; gap:12px; margin-bottom:12px">
                  <div style="width:48px; height:48px; border-radius:12px; background:linear-gradient(135deg,#f7b731,#e67e22); display:flex; align-items:center; justify-content:center; font-size:24px">⭐</div>
                  <div>
                    <div style="font-size:15px; font-weight:700; color:#f7b731">Licencja PRO aktywna</div>
                    <div style="font-size:11px; color:#94a3b8">Tier: <span id="settings-license-tier" style="color:#00d4ff; font-weight:700">PRO</span></div>
                  </div>
                </div>
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:12px">
                  <div style="padding:8px 12px; background:rgba(46,204,113,0.06); border:1px solid rgba(46,204,113,0.15); border-radius:8px">
                    <div style="font-size:9px; color:#64748b; text-transform:uppercase">Status</div>
                    <div style="font-size:13px; font-weight:700; color:#2ecc71">✅ Aktywna</div>
                  </div>
                  <div style="padding:8px 12px; background:rgba(0,212,255,0.06); border:1px solid rgba(0,212,255,0.15); border-radius:8px">
                    <div style="font-size:9px; color:#64748b; text-transform:uppercase">Funkcje</div>
                    <div style="font-size:11px; color:#00d4ff; font-weight:600">HEMS + AI + Blueprinty</div>
                  </div>
                </div>
                <div style="padding:10px; border-radius:8px; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06)">
                  <div style="font-size:10px; color:#94a3b8; line-height:1.6">
                    Aby zarządzać licencją (zmiana ustawień, usunięcie klucza), przejdź do:<br>
                    <strong style="color:#fff">Ustawienia → Urządzenia → Smarting HOME → Konfiguruj</strong>
                  </div>
                </div>
              </div>
            </div>

            <!-- ℹ️ Info -->
            <div class="card" style="grid-column: 1 / -1">
              <div class="card-title">ℹ️ Informacje</div>
              <div class="dr"><span class="lb">Wersja integracji</span><span class="vl">1.34.2</span></div>
              <div class="dr"><span class="lb">Ścieżka zdjęć</span><span class="vl" style="font-size:10px">/config/www/smartinghome/</span></div>
              <div class="dr"><span class="lb">Dokumentacja</span><span class="vl"><a href="https://smartinghome.pl/docs" target="_blank" style="color:#00d4ff">smartinghome.pl/docs</a></span></div>
              <div class="dr"><span class="lb">Wsparcie</span><span class="vl"><a href="https://github.com/GregECAT/smartinghome-homeassistant/issues" target="_blank" style="color:#00d4ff">GitHub Issues</a></span></div>
            </div>

          </div>
        </div>

        <!-- Custom Modal Overlay -->
        <div class="sh-modal-overlay" id="sh-modal-overlay" onclick="if(event.target===this) this.getRootNode().host._closeModal()">
          <div class="sh-modal" onclick="event.stopPropagation()">
            <div id="sh-modal-body"></div>
          </div>
        </div>

      </div>
    `;

    if (this._hass) this._updateAll();
    // Clear any previous interval before starting a new one
    if (this._interval) clearInterval(this._interval);
    this._interval = setInterval(() => { if (this._hass) this._updateAll(); }, 5000);
    // Connection watchdog — detect stale hass connection every 30s
    if (this._watchdog) clearInterval(this._watchdog);
    this._watchdog = setInterval(() => {
      if (!this._hass?.connection) return;
      try {
        this._hass.connection.ping().catch(() => {
          console.warn('[SH] Connection stale, recovering...');
          if (this._interval) clearInterval(this._interval);
          this._render();
          this._loadSettings();
          this._interval = setInterval(() => { if (this._hass) this._updateAll(); }, 5000);
        });
      } catch(e) {
        console.warn('[SH] Watchdog ping error:', e);
      }
    }, 30000);
  }

  /* ═══════════════════════════════════════════════════
     AUTOPILOT TAB — JavaScript Methods
     ═══════════════════════════════════════════════════ */

  _updateAutopilotVisibility() {
    const btn = this.shadowRoot.getElementById('tab-btn-autopilot');
    if (!btn) return;
    const tier = this._tier();
    const show = (tier === 'PRO' || tier === 'ENTERPRISE');
    btn.style.display = show ? 'inline-block' : 'none';
    if (show && !this._autopilotTabLogged) {
      console.log('[SH] Autopilot tab visible (tier=' + tier + ')');
      this._autopilotTabLogged = true;
    }
    // Update tier badge
    const tierBadge = this.shadowRoot.getElementById('ap-tier-badge');
    if (tierBadge) tierBadge.textContent = tier || 'FREE';
  }

  _updateAutopilot() {
    this._updateAutopilotVisibility();
    this._renderStrategyPresets();
    this._renderActionSections();
    this._updateAutopilotContext();
    this._loadAutopilotPlan();
    this._updateAutopilotProviderUI();
    this._updateLiveDecisionLog();
  }

  /* ── Action definitions (mirrors backend autopilot_actions.py) ── */
  _getActionDefs() {
    if (this._actionDefsCache) return this._actionDefsCache;

    const defs = {
      categories: [
        { id: 'w0_safety', label: '🛡️ W0: Safety Guard', color: '#e74c3c' },
        { id: 'w1_g13', label: '⚡ W1: Harmonogram taryfowy', color: '#f7b731' },
        { id: 'w2_rce', label: '💰 W2: RCE Dynamic Pricing', color: '#2ecc71' },
        { id: 'w3_soc', label: '🔋 W3: SOC Safety', color: '#00d4ff' },
        { id: 'w4_voltage', label: '⚡ W4: Napięcie', color: '#e74c3c' },
        { id: 'w4_surplus', label: '☀️ W4: Nadwyżka PV', color: '#f7b731' },
        { id: 'w5_weather', label: '🌧️ W5: Pogoda + Pre-peak', color: '#9b59b6' },
      ],
      actions: [
        // W0
        { id: 'grid_import_guard', cat: 'w0_safety', icon: '🛡️', name: 'Grid Import Guard', desc: 'STOP ładowania baterii z sieci w drogich godzinach. Wyjątek: RCE < 100 PLN/MWh.', always: true, slots: ['grid_power', 'battery_power'] },
        { id: 'pv_surplus_charge', cat: 'w0_safety', icon: '☀️🔋', name: 'PV Surplus → ładuj baterię', desc: 'Nadwyżka PV (export >300W) w drogich godz. → ładuj baterię.', always: true, slots: ['grid_power', 'battery_soc'] },
        // W1
        { id: 'sell_07', cat: 'w1_g13', icon: '☀️', name: 'Sprzedaż (07:00)', desc: 'G13 szczyt poranny (0.91 zł). Sprzedawaj 7-13 Pn-Pt.', slots: ['rce_price', 'battery_soc'] },
        { id: 'charge_13', cat: 'w1_g13', icon: '🔋', name: 'Ładowanie (13:00)', desc: 'Off-peak (0.63 zł). Ładuj baterię 13:00-szczyt Pn-Pt.', slots: ['rce_price', 'battery_soc'] },
        { id: 'evening_peak', cat: 'w1_g13', icon: '💰', name: 'Szczyt wieczorny', desc: 'G13 szczyt (1.50 zł). Bateria zasila dom.', slots: ['g13_zone', 'battery_soc'] },
        { id: 'weekend', cat: 'w1_g13', icon: '🏖️', name: 'Weekend', desc: 'Off-peak cały dzień → autokonsumpcja.', slots: ['rce_price'] },
        // W2
        { id: 'night_arbitrage', cat: 'w2_rce', icon: '🌙', name: 'Arbitraż nocny', desc: 'Nocne ładowanie z sieci (0.63 → 1.50 zł) gdy słaba prognoza PV.', slots: ['forecast_tomorrow', 'battery_soc'] },
        { id: 'cheapest_window', cat: 'w2_rce', icon: '🟢', name: 'Najtańsze okno → ładuj', desc: 'Najtańsze okno PSE aktywne → ładuj baterię.', slots: ['rce_cheapest', 'battery_soc'] },
        { id: 'most_expensive_window', cat: 'w2_rce', icon: '🔴', name: 'Najdroższe okno → alert', desc: 'Najdroższe okno aktywne → bateria zasila dom.', slots: ['rce_price', 'g13_zone'] },
        { id: 'low_price_charge', cat: 'w2_rce', icon: '📉', name: 'Niska cena → ładuj', desc: 'RCE < 150 PLN/MWh → nie opłaca się sprzedawać.', slots: ['rce_price', 'rce_trend'] },
        { id: 'high_price_sell', cat: 'w2_rce', icon: '📈', name: 'Cena wzrosła → sprzedaj', desc: 'RCE > 300 PLN/MWh → opłaca się sprzedawać.', slots: ['rce_price', 'rce_trend'] },
        { id: 'rce_peak_g13', cat: 'w2_rce', icon: '💰💰', name: 'RCE Peak + G13 Szczyt', desc: 'RCE > 500 + G13 szczyt → max zysk!', slots: ['rce_price', 'battery_soc'] },
        { id: 'negative_price', cat: 'w2_rce', icon: '🤑', name: 'Ujemna cena → DARMOWA!', desc: 'RCE ujemna → darmowa energia! Ładuj + bojler ON.', slots: ['rce_price', 'boiler'] },
        // W3
        { id: 'soc_check_11', cat: 'w3_soc', icon: '⚠️', name: 'SOC check 11:00', desc: 'SOC < 50% o 11:00 → ładuj PV. NIE z sieci w szczycie!', always: true, slots: ['battery_soc'] },
        { id: 'soc_check_12', cat: 'w3_soc', icon: '⚠️', name: 'SOC check 12:00', desc: 'SOC < 70% o 12:00. PV → bateria. Brak → 13:00 off-peak.', always: true, slots: ['battery_soc'] },
        { id: 'smart_soc_protection', cat: 'w3_soc', icon: '🔋', name: 'Smart SOC Protection', desc: 'SOC < 20%: drogie=PV-only, tanie=ładuj normalnie.', always: true, slots: ['battery_soc'] },
        { id: 'soc_emergency', cat: 'w3_soc', icon: '🚨', name: 'EMERGENCY SOC < 5%', desc: 'Ładuj awaryjnie NIEZALEŻNIE od taryfy do 15%!', always: true, slots: ['battery_soc'] },
        // W4 Voltage
        { id: 'voltage_boiler', cat: 'w4_voltage', icon: '⚡', name: 'Napięcie → Bojler', desc: '>252V → Bojler ON.', always: true, slots: ['voltage_l1', 'boiler'] },
        { id: 'voltage_klima', cat: 'w4_voltage', icon: '⚡⚡', name: 'Napięcie → Klima', desc: '>253V → Klima ON (bojler już działa).', always: true, slots: ['voltage_l1', 'ac'] },
        { id: 'voltage_critical', cat: 'w4_voltage', icon: '🔴', name: 'Krytyczne napięcie', desc: '>254V → Ładuj baterię natychmiast!', always: true, slots: ['voltage_l1', 'battery_soc'] },
        // W4 Surplus
        { id: 'surplus_boiler', cat: 'w4_surplus', icon: '☀️', name: 'Nadwyżka → Bojler', desc: '>2kW nadwyżki + SOC >80% → Bojler ON.', always: true, slots: ['pv_surplus', 'battery_soc'] },
        { id: 'surplus_klima', cat: 'w4_surplus', icon: '❄️', name: 'Nadwyżka → Klima', desc: '>3kW nadwyżki + SOC >85% → Klima ON.', always: true, slots: ['pv_surplus', 'battery_soc'] },
        { id: 'surplus_gniazdko', cat: 'w4_surplus', icon: '🔌', name: 'Nadwyżka → Gniazdko 2', desc: '>4kW nadwyżki + SOC >90% → Gniazdko ON.', always: true, slots: ['pv_surplus', 'battery_soc'] },
        { id: 'surplus_emergency_off', cat: 'w4_surplus', icon: '🚨', name: 'Awaryjne OFF', desc: 'SOC < 50% → wyłącz obciążenia.', always: true, slots: ['battery_soc'] },
        // W5
        { id: 'morning_check_0530', cat: 'w5_weather', icon: '🌧️⚡', name: '05:30 poranny check', desc: 'SOC <80% + PV <10kWh → ładuj z sieci do 07:00.', slots: ['forecast_today', 'battery_soc'] },
        { id: 'ecowitt_check_1000', cat: 'w5_weather', icon: '🌥️', name: '10:00 weryfikacja Ecowitt', desc: 'SOC <60% + niska radiacja → priorytet PV→bateria.', slots: ['solar_radiation'] },
        { id: 'last_chance_1330', cat: 'w5_weather', icon: '⚠️🔋', name: '13:30 ostatnia szansa', desc: 'SOC <80% + PV rem <5kWh → ładuj! Szczyt za 2.5h.', slots: ['forecast_remaining', 'battery_soc'] },
        { id: 'prepeak_summer_1800', cat: 'w5_weather', icon: '☀️⚠️', name: '18:00 pre-peak lato', desc: 'SOC <70% + słaba radiacja → ładuj! Szczyt o 19:00.', slots: ['solar_radiation', 'battery_soc'] },
        { id: 'sudden_clouds', cat: 'w5_weather', icon: '☁️', name: 'Nagłe zachmurzenie', desc: 'Radiacja <50 W/m² + SOC <70% → priorytet bateria.', slots: ['solar_radiation', 'pv_power'] },
        { id: 'rain_priority', cat: 'w5_weather', icon: '🌧️', name: 'Deszcz → priorytet', desc: 'Opady >0.5mm/h + SOC <70% → PV → bateria.', slots: ['rain_rate', 'pv_power'] },
        { id: 'weak_forecast_dod', cat: 'w5_weather', icon: '🌧️', name: 'Słaba prognoza → DOD', desc: 'Jutro < 5 kWh → zachowaj baterię (DOD → 70%).', slots: ['forecast_tomorrow', 'dod'] },
        { id: 'restore_dod', cat: 'w5_weather', icon: '☀️', name: 'Przywróć DOD', desc: 'PV > 500W przez 10 min → DOD z powrotem na 95%.', always: true, slots: ['pv_power'] },
      ],
      // Strategy → action IDs mapping
      presets: {
        max_self_consumption: ['weekend'],
        max_profit: ['sell_07', 'charge_13', 'evening_peak', 'weekend', 'night_arbitrage', 'cheapest_window', 'most_expensive_window', 'low_price_charge', 'high_price_sell', 'rce_peak_g13', 'negative_price', 'morning_check_0530', 'ecowitt_check_1000', 'last_chance_1330', 'prepeak_summer_1800', 'sudden_clouds', 'rain_priority', 'weak_forecast_dod'],
        battery_protection: ['weekend', 'weak_forecast_dod'],
        zero_export: ['weekend'],
        weather_adaptive: ['sell_07', 'charge_13', 'evening_peak', 'weekend', 'night_arbitrage', 'low_price_charge', 'high_price_sell', 'morning_check_0530', 'ecowitt_check_1000', 'last_chance_1330', 'prepeak_summer_1800', 'sudden_clouds', 'rain_priority', 'weak_forecast_dod'],
        ai_full_autonomy: ['sell_07', 'charge_13', 'evening_peak', 'weekend', 'night_arbitrage', 'cheapest_window', 'most_expensive_window', 'low_price_charge', 'high_price_sell', 'rce_peak_g13', 'negative_price', 'morning_check_0530', 'ecowitt_check_1000', 'last_chance_1330', 'prepeak_summer_1800', 'sudden_clouds', 'rain_priority', 'weak_forecast_dod'],
      },
    };
    this._actionDefsCache = defs;
    return defs;
  }

  /* ── Render compact strategy preset buttons ── */
  _renderStrategyPresets() {
    const container = this.shadowRoot.getElementById('ap-strategy-presets');
    if (!container) return;

    const strategies = [
      { id: 'max_self_consumption', icon: '🟢', name: 'Max Autokonsumpcja' },
      { id: 'max_profit', icon: '💰', name: 'Max Zysk' },
      { id: 'battery_protection', icon: '🔋', name: 'Ochrona Bat.' },
      { id: 'zero_export', icon: '⚡', name: 'Zero Export' },
      { id: 'weather_adaptive', icon: '🌧️', name: 'Pogodowy' },
      { id: 'ai_full_autonomy', icon: '🧠', name: 'AI Pełna' },
    ];

    const active = this._autopilotActiveStrategy || null;

    container.innerHTML = strategies.map(s => {
      const isActive = s.id === active;
      const bg = isActive ? 'rgba(46,204,113,0.15)' : 'rgba(255,255,255,0.04)';
      const border = isActive ? '1px solid rgba(46,204,113,0.4)' : '1px solid rgba(255,255,255,0.08)';
      const badge = isActive ? '<span style="font-size:7px; background:#2ecc71; color:#0a1628; padding:1px 5px; border-radius:8px; font-weight:800; margin-left:4px; animation:pulse 2s infinite">ACTIVE</span>' : '';
      return `<button style="padding:6px 12px; border-radius:8px; background:${bg}; border:${border}; color:#f8fafc; font-size:11px; font-weight:600; cursor:pointer; transition:all 0.2s; display:flex; align-items:center; gap:4px; white-space:nowrap"
        onclick="this.getRootNode().host._switchAutopilotStrategy('${s.id}')"
        title="${s.name}">${s.icon} ${s.name}${badge}</button>`;
    }).join('');
  }

  /* ── Show strategy help modal ── */
  _showStrategyHelp() {
    // Remove existing modal if any
    const existing = this.shadowRoot.getElementById('strategy-help-modal');
    if (existing) { existing.remove(); return; }

    const strategies = [
      {
        icon: '🟢', name: 'Max Autokonsumpcja',
        subtitle: 'Maksymalne zużycie własne',
        soc: '10% – 100%',
        desc: 'Cała energia z paneli PV jest kierowana do zasilania domu i ładowania baterii. Zero eksportu do sieci (chyba że bateria pełna). Zero importu z sieci (chyba że SOC krytycznie niski). Idealna strategia gdy nie opłaca się sprzedawać do sieci.',
        actions: ['PV → dom + bateria', 'Brak handlu z siecią', 'Bojler/klima z nadwyżki PV'],
        best: 'Dla prosumentów bez net-billingu lub z niskimi cenami sprzedaży.',
        color: '#2ecc71'
      },
      {
        icon: '💰', name: 'Max Zysk (Arbitraż)',
        subtitle: 'Kupuj tanio, sprzedawaj drogo',
        soc: '15% – 100%',
        desc: 'Agresywna strategia cenowa. Ładuje baterię w tanich strefach G13 (off-peak 0.63 zł/kWh) i rozładowuje w drogich (afternoon peak 1.50 zł/kWh). Wykorzystuje RCE do dynamicznego handlu. Margin na cykl: ~0.87 zł/kWh.',
        actions: ['Off-peak → force_charge z sieci', 'Peak → force_discharge + sprzedaż', 'RCE > 0.63 → sprzedaj, RCE < 0.63 → zachowaj', 'Min 20% SOC buffer na przetrwanie szczytu'],
        best: 'Dla taryfy G13 z dużą baterią. Max oszczędności finansowe.',
        color: '#f7b731'
      },
      {
        icon: '🔋', name: 'Ochrona Baterii',
        subtitle: 'Łagodne cykle, długa żywotność',
        soc: '30% – 80%',
        desc: 'Zachowawcza strategia chroniąca żywotność baterii. SOC zawsze w bezpiecznym zakresie 30-80% (rekomendacja producentów Li-ion). DOD ograniczony do 70%. Unika wymuszonych ładowań/rozładowań. Bateria pracuje tylko jako bufor PV.',
        actions: ['DOD = 70%', 'Brak force_charge/discharge', 'PV → dom, nadwyżka → bateria', 'Minimalne cyklowanie'],
        best: 'Dla nowych baterii lub gdy żywotność jest priorytetem nad oszczędnościami.',
        color: '#3498db'
      },
      {
        icon: '⚡', name: 'Zero Export',
        subtitle: 'Żadna energia nie trafia do sieci',
        soc: '10% – 100%',
        desc: 'Całkowity zakaz eksportu do sieci. Limit eksportu ustawiony na 0W. Cała energia PV jest kierowana do domu, baterii i obciążeń zarządzanych. Przydatna gdy operator sieci ogranicza eksport lub gdy brak umowy prosumenckiej.',
        actions: ['set_export_limit(0)', 'PV → dom → bateria → bojler/klima', 'Brak sprzedaży', 'Automatyczne zarządzanie obciążeniami'],
        best: 'Gdy operator zabrania eksportu lub gdy brak korzystnej umowy sprzedaży.',
        color: '#e74c3c'
      },
      {
        icon: '🌧️', name: 'Pogodowy Adaptacyjny',
        subtitle: 'Dynamiczna adaptacja do pogody',
        soc: '15% – 95%',
        desc: 'AI analizuje prognozę pogody i dynamicznie zmienia zachowanie godzina po godzinie. Słaba prognoza PV + tania strefa = ładowanie z sieci. Dobra pogoda = PV pokryje zapotrzebowanie. Przed szczytem sprawdza czy bateria jest gotowa.',
        actions: ['Zła pogoda + off-peak → force_charge', 'Dobra pogoda → PV samowystarczalne', 'Przed szczytem: SOC < 80% → ładuj', 'Automatyczne stop_force po zmianie strefy'],
        best: 'Dla zmiennego klimatu. Balans między oszczędnościami a bezpieczeństwem.',
        color: '#9b59b6'
      },
      {
        icon: '🧠', name: 'AI Pełna Autonomia',
        subtitle: 'AI sam decyduje o wszystkim',
        soc: '10% – 100%',
        desc: 'Pełna autonomia AI. Agent energetyczny sam wybiera optymalną taktykę dla każdej godziny: arbitraż, autokonsumpcja, sprzedaż lub ładowanie z sieci. Wykorzystuje model matematyczny, prognozę pogody, ceny RCE i G13 do podejmowania decyzji.',
        actions: ['Dostęp do wszystkich 35 akcji', 'Dynamiczne mieszanie strategii', 'Automatyczne stop_force po operacjach', 'Max 6h trybu wymuszonego'],
        best: 'Dla zaawansowanych użytkowników. Wymaga skonfigurowanego dostawcy AI.',
        color: '#1abc9c'
      }
    ];

    const modal = document.createElement('div');
    modal.id = 'strategy-help-modal';
    modal.style.cssText = 'position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.7); backdrop-filter:blur(8px); z-index:9999; display:flex; align-items:center; justify-content:center; padding:20px; animation:fadeIn 0.2s ease';
    modal.onclick = (e) => { if (e.target === modal) modal.remove(); };

    modal.innerHTML = `
      <div style="background:linear-gradient(145deg, #1e293b, #0f172a); border:1px solid rgba(255,255,255,0.1); border-radius:20px; max-width:680px; width:100%; max-height:85vh; overflow-y:auto; padding:28px; box-shadow:0 25px 50px rgba(0,0,0,0.5)">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px">
          <div>
            <div style="font-size:18px; font-weight:700; color:#f8fafc">📋 Strategie Autopilota</div>
            <div style="font-size:12px; color:#64748b; margin-top:4px">Każda strategia definiuje jakie akcje i reguły są aktywne</div>
          </div>
          <button onclick="this.closest('#strategy-help-modal').remove()" style="background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.12); border-radius:10px; width:32px; height:32px; color:#94a3b8; font-size:16px; cursor:pointer">✕</button>
        </div>
        ${strategies.map(s => `
          <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.06); border-radius:14px; padding:18px; margin-bottom:12px; border-left:3px solid ${s.color}">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:10px">
              <div>
                <div style="font-size:15px; font-weight:700; color:#f8fafc">${s.icon} ${s.name}</div>
                <div style="font-size:11px; color:${s.color}; font-weight:600; margin-top:2px">${s.subtitle}</div>
              </div>
              <div style="background:rgba(255,255,255,0.06); padding:3px 10px; border-radius:8px; font-size:10px; color:#94a3b8; white-space:nowrap">SOC: ${s.soc}</div>
            </div>
            <div style="font-size:12px; color:#cbd5e1; line-height:1.7; margin-bottom:12px">${s.desc}</div>
            <div style="display:flex; flex-wrap:wrap; gap:6px; margin-bottom:10px">
              ${s.actions.map(a => `<span style="font-size:10px; background:rgba(255,255,255,0.06); color:#94a3b8; padding:3px 8px; border-radius:6px">▸ ${a}</span>`).join('')}
            </div>
            <div style="font-size:11px; color:#64748b; font-style:italic">💡 ${s.best}</div>
          </div>
        `).join('')}
      </div>
    `;

    this.shadowRoot.appendChild(modal);
  }

  /* ── Render action sections (W0-W5) ── */
  _renderActionSections() {
    const container = this.shadowRoot.getElementById('ap-action-sections');
    if (!container) return;

    // Avoid re-rendering if already populated (except first time or strategy change)
    const newKey = this._autopilotActiveStrategy || 'none';
    if (this._lastRenderedActionsKey === newKey && container.children.length > 0) return;
    this._lastRenderedActionsKey = newKey;

    const defs = this._getActionDefs();
    const active = this._autopilotActiveStrategy || null;
    const presetActions = active ? new Set(defs.presets[active] || []) : new Set();

    // Build HTML for each category
    let html = '';
    for (const cat of defs.categories) {
      const catActions = defs.actions.filter(a => a.cat === cat.id);
      if (catActions.length === 0) continue;

      const activeInCat = catActions.filter(a => a.always || presetActions.has(a.id));
      const countLabel = `${activeInCat.length}/${catActions.length} aktywnych`;

      html += `
        <div class="card" style="margin-bottom:10px; padding:10px 14px">
          <div style="display:flex; justify-content:space-between; align-items:center; cursor:pointer"
               onclick="this.getRootNode().host._toggleApSection('${cat.id}')">
            <div style="font-size:11px; font-weight:700; color:${cat.color}; letter-spacing:0.3px">${cat.label}</div>
            <div style="display:flex; align-items:center; gap:6px">
              <span style="font-size:9px; color:#64748b">${countLabel}</span>
              <span id="ap-cat-arrow-${cat.id}" style="font-size:9px; color:#64748b; transition:transform 0.2s">▼</span>
            </div>
          </div>
          <div id="ap-cat-body-${cat.id}" style="display:grid; grid-template-columns:repeat(auto-fill, minmax(170px, 1fr)); gap:6px; margin-top:8px">
            ${catActions.map(a => this._renderActionCard(a, presetActions, cat.color)).join('')}
          </div>
        </div>`;
    }

    container.innerHTML = html;
  }

  _renderActionCard(action, presetActions, catColor) {
    const isActive = action.always || presetActions.has(action.id);
    // Use backend state (from _evaluate_actions) as primary source
    const backendStatus = this._actionStates && this._actionStates[action.id];
    const isDisabled = backendStatus === 'disabled';
    const isLive = !isDisabled && (backendStatus === 'active' || (this._activeActions && this._activeActions.has(action.id)));
    
    let statusLabel, statusBg, statusBorder, statusTextColor;
    if (isDisabled) {
      statusLabel = 'WYŁĄCZ.';
      statusBg = 'rgba(231,76,60,0.15)';
      statusBorder = 'rgba(231,76,60,0.3)';
      statusTextColor = '#e74c3c';
    } else if (isLive) {
      statusLabel = 'AKTYWNE';
      statusBg = 'rgba(46,204,113,0.2)';
      statusBorder = 'rgba(46,204,113,0.5)';
      statusTextColor = '#2ecc71';
    } else if (isActive) {
      statusLabel = 'CZEKA';
      statusBg = 'rgba(148,163,184,0.15)';
      statusBorder = 'rgba(148,163,184,0.35)';
      statusTextColor = '#94a3b8';
    } else {
      statusLabel = 'IDLE';
      statusBg = 'rgba(71,85,105,0.2)';
      statusBorder = 'rgba(71,85,105,0.3)';
      statusTextColor = '#64748b';
    }

    const borderColor = isDisabled ? 'rgba(231,76,60,0.3)' : (isLive ? '#2ecc71' : (isActive ? catColor : 'rgba(255,255,255,0.06)'));
    const bgColor = isDisabled ? 'rgba(231,76,60,0.04)' : (isLive ? 'rgba(46,204,113,0.06)' : (isActive ? 'rgba(255,255,255,0.035)' : 'rgba(255,255,255,0.015)'));
    const opacity = isDisabled ? '0.5' : ((isActive || isLive) ? '1' : '0.55');
    const alwaysBadge = action.always
      ? `<span style="font-size:7px; background:rgba(231,76,60,0.2); color:#e74c3c; padding:1px 4px; border-radius:4px; font-weight:700; margin-left:auto">ALWAYS</span>`
      : '';

    // Truncate description to 60 chars
    const shortDesc = action.desc.length > 60 ? action.desc.substring(0, 57) + '…' : action.desc;

    return `
      <div class="ap-card" id="ap-action-${action.id}"
           style="opacity:${opacity}; border-left:3px solid ${borderColor}; padding:8px 10px; border-radius:8px; background:${bgColor}; cursor:pointer; transition:all 0.3s; display:flex; flex-direction:column; gap:4px"
           onclick="this.getRootNode().host._triggerAction('${action.id}')"
           title="${action.desc}">
        <div style="display:flex; align-items:center; gap:5px">
          <span style="font-size:16px">${action.icon}</span>
          <span style="font-size:10px; font-weight:700; color:#f8fafc; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap">${action.name}</span>
          ${alwaysBadge}
          <span data-status-badge style="font-size:7px; padding:1px 5px; border-radius:4px; font-weight:700; background:${statusBg}; border:1px solid ${statusBorder}; color:${statusTextColor}">${statusLabel}</span>
        </div>
        <div style="font-size:8px; color:#94a3b8; line-height:1.3; min-height:18px">${shortDesc}</div>
      </div>`;
  }

  _toggleApSection(catId) {
    const body = this.shadowRoot.getElementById(`ap-cat-body-${catId}`);
    const arrow = this.shadowRoot.getElementById(`ap-cat-arrow-${catId}`);
    if (!body) return;
    const visible = body.style.display !== 'none';
    body.style.display = visible ? 'none' : 'flex';
    if (arrow) arrow.textContent = visible ? '▶' : '▼';
  }

  _updateActionBadgesFromState() {
    // Update badges in-place without full re-render
    if (!this._actionStates) return;
    for (const [actionId, status] of Object.entries(this._actionStates)) {
      const card = this.shadowRoot.getElementById(`ap-action-${actionId}`);
      if (!card) continue;
      const badge = card.querySelector('[data-status-badge]');
      if (!badge) continue;

      if (status === 'active') {
        badge.textContent = 'AKTYWNE';
        badge.style.background = 'rgba(46,204,113,0.2)';
        badge.style.borderColor = 'rgba(46,204,113,0.5)';
        badge.style.color = '#2ecc71';
        card.style.borderLeftColor = '#2ecc71';
        card.style.background = 'rgba(46,204,113,0.06)';
        card.style.opacity = '1';
      } else if (status === 'waiting') {
        badge.textContent = 'CZEKA';
        badge.style.background = 'rgba(148,163,184,0.15)';
        badge.style.borderColor = 'rgba(148,163,184,0.35)';
        badge.style.color = '#94a3b8';
        card.style.borderLeftColor = '';
        card.style.opacity = '1';
      } else if (status === 'idle') {
        badge.textContent = 'IDLE';
        badge.style.background = 'rgba(71,85,105,0.2)';
        badge.style.borderColor = 'rgba(71,85,105,0.3)';
        badge.style.color = '#64748b';
        card.style.opacity = '0.55';
      } else if (status === 'disabled') {
        badge.textContent = 'WYŁĄCZ.';
        badge.style.background = 'rgba(231,76,60,0.15)';
        badge.style.borderColor = 'rgba(231,76,60,0.3)';
        badge.style.color = '#e74c3c';
        card.style.borderLeftColor = 'rgba(231,76,60,0.3)';
        card.style.background = 'rgba(231,76,60,0.04)';
        card.style.opacity = '0.5';
      }
    }
  }

  async _triggerAction(actionId) {
    const tile = this.shadowRoot.getElementById(`ap-action-${actionId}`);
    const origBg = tile ? tile.style.background : '';

    // Toggle: if action is currently active → deactivate it via backend
    const badge = tile ? tile.querySelector('[data-status-badge]') : null;
    const isActive = (badge && badge.textContent === 'AKTYWNE')
      || (this._activeActions && this._activeActions.has(actionId));

    if (isActive) {
      // ── DEACTIVATE ──
      if (tile) tile.style.background = 'rgba(231,76,60,0.15)';
      try {
        await this._hass.callService('smartinghome', 'toggle_autopilot_action', {
          action_id: actionId,
          enabled: false,
        });
        if (tile) {
          tile.style.opacity = '0.55';
          tile.style.borderLeftColor = 'rgba(255,255,255,0.06)';
          tile.style.background = 'rgba(255,255,255,0.015)';
        }
        if (badge) {
          badge.textContent = 'WYŁĄCZ.';
          badge.style.background = 'rgba(231,76,60,0.15)';
          badge.style.borderColor = 'rgba(231,76,60,0.3)';
          badge.style.color = '#e74c3c';
        }
        if (this._activeActions) this._activeActions.delete(actionId);
        console.log('[SH] Action deactivated:', actionId);
        this._updateLiveDecisionLog();
      } catch (err) {
        console.error('[SH] Deactivate action failed:', err);
        if (tile) { tile.style.background = origBg; }
      }
      return;
    }

    // ── ACTIVATE ──
    if (tile) tile.style.background = 'rgba(46,204,113,0.15)';

    try {
      await this._hass.callService('smartinghome', 'toggle_autopilot_action', {
        action_id: actionId,
        enabled: true,
      });
      await this._hass.callService('smartinghome', 'trigger_autopilot_action', {
        action_id: actionId,
      });
      if (tile) {
        tile.style.background = 'rgba(46,204,113,0.25)';
        tile.style.opacity = '1';
        tile.style.borderLeftColor = '#2ecc71';
      }
      if (badge) {
        badge.textContent = 'AKTYWNE';
        badge.style.background = 'rgba(46,204,113,0.2)';
        badge.style.borderColor = 'rgba(46,204,113,0.5)';
        badge.style.color = '#2ecc71';
      }
      if (!this._activeActions) this._activeActions = new Set();
      this._activeActions.add(actionId);
      setTimeout(() => { tile.style.background = origBg; }, 2000);
      this._updateLiveDecisionLog();
    } catch (err) {
      console.error('[SH] Trigger action failed:', err);
      if (tile) { tile.style.background = 'rgba(231,76,60,0.2)'; setTimeout(() => { tile.style.background = origBg; }, 1500); }
    }
  }

  async _switchAutopilotStrategy(strategyId) {
    // Toggle: if clicking already-active strategy → deactivate
    if (this._autopilotActiveStrategy === strategyId) {
      return this._deactivateAutopilot();
    }
    this._autopilotActiveStrategy = strategyId;
    this._lastRenderedActionsKey = null;  // force re-render
    this._renderStrategyPresets();
    this._renderActionSections();

    // Update status badge
    const statusEl = this.shadowRoot.getElementById('ap-status');
    if (statusEl) { statusEl.textContent = '⏳ AKTYWUJĘ...'; statusEl.style.color = '#f7b731'; }

    try {
      // Call backend service to activate strategy
      await this._hass.callService('smartinghome', 'set_autopilot_strategy', {
        strategy: strategyId,
      });
      console.log('[SH] Strategy activated:', strategyId);
      if (statusEl) { statusEl.textContent = '● AKTYWNY'; statusEl.style.color = '#2ecc71'; }
    } catch (err) {
      console.error('[SH] Strategy activation failed:', err);
      if (statusEl) { statusEl.textContent = '❌ BŁĄD'; statusEl.style.color = '#e74c3c'; }
    }

    // Clear previous estimation results
    const timeline = this.shadowRoot.getElementById('ap-timeline');
    if (timeline) timeline.innerHTML = '<div style="width:100%; display:flex; align-items:center; justify-content:center; color:#64748b; font-size:11px">Strategia aktywna — sterowanie automatyczne co 30s</div>';

    // Show deactivate button
    const deactBtn = this.shadowRoot.getElementById('ap-deactivate-btn');
    if (deactBtn) deactBtn.style.display = 'inline-block';
  }

  async _deactivateAutopilot() {
    const statusEl = this.shadowRoot.getElementById('ap-status');
    if (statusEl) { statusEl.textContent = '⏳ WYŁĄCZAM...'; statusEl.style.color = '#f7b731'; }

    try {
      await this._hass.callService('smartinghome', 'deactivate_autopilot', {});
      console.log('[SH] Autopilot deactivated');

      this._autopilotActiveStrategy = null;
      this._lastRenderedActionsKey = null; this._renderStrategyPresets(); this._renderActionSections();

      if (statusEl) { statusEl.textContent = '● GOTOWY'; statusEl.style.color = '#64748b'; }

      // Hide deactivate button
      const deactBtn = this.shadowRoot.getElementById('ap-deactivate-btn');
      if (deactBtn) deactBtn.style.display = 'none';

      // Show restored automations message
      const timeline = this.shadowRoot.getElementById('ap-timeline');
      if (timeline) timeline.innerHTML = '<div style="width:100%; display:flex; align-items:center; justify-content:center; color:#2ecc71; font-size:11px">✅ Autopilot wyłączony — automatyzacje przywrócone</div>';
    } catch (err) {
      console.error('[SH] Deactivation failed:', err);
      if (statusEl) { statusEl.textContent = '❌ BŁĄD'; statusEl.style.color = '#e74c3c'; }
    }
  }

  _updateAutopilotContext() {
    const d = this._hass ? this._hass.states : {};
    const g = (id) => { const s = d[id]; return s && s.state !== 'unavailable' && s.state !== 'unknown' ? s.state : null; };

    const smap = this._sensorMap || {};
    const pvForecast = g('sensor.energy_production_today') || g('sensor.energy_production_today_2');
    const soc = g(smap.battery_soc || 'sensor.battery_state_of_charge');
    const rce = g('sensor.rce_pse_cena');

    const setEl = (id, val) => { const el = this.shadowRoot.getElementById(id); if (el) el.textContent = val; };

    setEl('ap-ctx-pv', pvForecast ? `${parseFloat(pvForecast).toFixed(1)} kWh` : '— kWh');
    setEl('ap-ctx-soc', soc ? `${parseFloat(soc).toFixed(0)}%` : '—%');
    setEl('ap-ctx-rce', rce ? `${parseFloat(rce).toFixed(0)} zł/MWh` : '— zł');

    // Tariff zone (dynamic)
    const tInfo = this._getTariffInfo();
    const apLabel = this.shadowRoot.getElementById('ap-ctx-g13-label');
    if (apLabel) apLabel.textContent = `⏰ Strefa ${tInfo.tariff}`;
    setEl('ap-ctx-g13', tInfo.zoneName);
    const apVal = this.shadowRoot.getElementById('ap-ctx-g13');
    if (apVal) apVal.style.color = tInfo.zoneColor;

    // Weather
    const weather = d['weather.dom'];
    if (weather) {
      setEl('ap-ctx-temp', `${weather.attributes.temperature || '—'}°C`);
      setEl('ap-ctx-clouds', `${weather.attributes.cloud_coverage || '—'}%`);
    }
  }

  _updateAutopilotProviderUI() {
    const providerSelect = this.shadowRoot.getElementById('ap-provider-select');
    const modelSelect = this.shadowRoot.getElementById('ap-model-select');
    if (!providerSelect || !modelSelect) return;

    // Load saved provider preference
    try {
      const settingsUrl = '/local/smartinghome/settings.json';
      fetch(settingsUrl + '?t=' + Date.now()).then(r => r.json()).then(s => {
        const savedProvider = s.default_ai_provider || 'gemini';
        providerSelect.value = savedProvider;
        this._updateAutopilotModelOptions(savedProvider);

        const savedModel = savedProvider === 'gemini' ? s.gemini_model : s.anthropic_model;
        if (savedModel) modelSelect.value = savedModel;

        // Update provider badge
        const badge = this.shadowRoot.getElementById('ap-provider-badge');
        if (badge) badge.textContent = savedProvider === 'gemini' ? 'Gemini' : 'Claude';
      }).catch(() => {});
    } catch (e) {}
  }

  _onAutopilotProviderChange() {
    const providerSelect = this.shadowRoot.getElementById('ap-provider-select');
    if (!providerSelect) return;
    this._updateAutopilotModelOptions(providerSelect.value);
    const badge = this.shadowRoot.getElementById('ap-provider-badge');
    if (badge) badge.textContent = providerSelect.value === 'gemini' ? 'Gemini' : 'Claude';
  }

  _updateAutopilotModelOptions(provider) {
    const modelSelect = this.shadowRoot.getElementById('ap-model-select');
    if (!modelSelect) return;

    const models = {
      gemini: [
        { value: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash (szybki)' },
        { value: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro (zaawansowany)' },
        { value: 'gemini-3-flash-preview', label: 'Gemini 3 Flash (Preview)' },
      ],
      anthropic: [
        { value: 'claude-sonnet-4-6', label: 'Claude Sonnet 4.6 (szybki)' },
        { value: 'claude-opus-4-6', label: 'Claude Opus 4.6 (najpotężniejszy)' },
        { value: 'claude-3-5-haiku', label: 'Claude Haiku 3.5 (najtańszy)' },
      ],
    };

    const options = models[provider] || models.gemini;
    modelSelect.innerHTML = options.map(m => `<option value="${m.value}">${m.label}</option>`).join('');
  }

  async _runAutopilotEstimation() {
    const strategy = this._autopilotActiveStrategy || 'max_self_consumption';
    const providerSelect = this.shadowRoot.getElementById('ap-provider-select');
    const provider = providerSelect ? providerSelect.value : 'auto';

    // Visual feedback — disable button and show spinner
    const btn = this.shadowRoot.querySelector('[onclick*="_runAutopilotEstimation"]');
    const btnOrigText = btn ? btn.innerHTML : '';
    if (btn) {
      btn.disabled = true;
      btn.style.opacity = '0.6';
      btn.style.cursor = 'wait';
      btn.innerHTML = '⏳ Analizuję...';
    }

    // Update status
    const statusEl = this.shadowRoot.getElementById('ap-status');
    if (statusEl) { statusEl.textContent = '⏳ ANALIZUJĘ...'; statusEl.style.color = '#f7b731'; }

    try {
      await this._hass.callService('smartinghome', 'run_autopilot', {
        strategy: strategy,
        provider: provider,
        with_ai: true,
      });

      // Button success state
      if (btn) { btn.innerHTML = '✅ Gotowe!'; }

      // Wait for result event via polling settings.json
      setTimeout(() => this._loadAutopilotPlan(), 2000);
      setTimeout(() => this._loadAutopilotPlan(), 8000);
      setTimeout(() => this._loadAutopilotPlan(), 15000);
      setTimeout(() => this._loadAutopilotPlan(), 30000);

      // Restore button after 3s
      setTimeout(() => {
        if (btn) {
          btn.disabled = false;
          btn.style.opacity = '1';
          btn.style.cursor = 'pointer';
          btn.innerHTML = btnOrigText;
        }
      }, 3000);
    } catch (e) {
      if (btn) {
        btn.innerHTML = '❌ Błąd!';
        setTimeout(() => {
          btn.disabled = false;
          btn.style.opacity = '1';
          btn.style.cursor = 'pointer';
          btn.innerHTML = btnOrigText;
        }, 3000);
      }
      if (statusEl) { statusEl.textContent = '❌ BŁĄD'; statusEl.style.color = '#e74c3c'; }
    }
  }



  async _loadAutopilotPlan() {
    try {
      const r = await fetch('/local/smartinghome/settings.json?t=' + Date.now());
      const s = await r.json();
      const plan = s.ai_autopilot_plan;
      if (!plan || !plan.hourly_plan) return;

      // Dedup guard — don't re-process the same estimation
      const planKey = `${plan.strategy}_${plan.timestamp || ''}`;
      if (this._lastProcessedPlanKey === planKey) return;
      this._lastProcessedPlanKey = planKey;

      // Update status
      const statusEl = this.shadowRoot.getElementById('ap-status');
      if (statusEl) { statusEl.textContent = '● AKTYWNY'; statusEl.style.color = '#2ecc71'; }

      // Update strategy savings across cards — show vs_no_management (meaningful metric)
      if (plan.strategy) {
        const savingsEl = this.shadowRoot.getElementById(`ap-savings-${plan.strategy}`);
        if (savingsEl) {
          const vs = plan.vs_no_management || 0;
          savingsEl.textContent = vs >= 0 ? `+${vs.toFixed(2)} zł` : `${vs.toFixed(2)} zł`;
          savingsEl.style.color = vs >= 0 ? '#2ecc71' : '#e74c3c';
        }
      }

      // Render 24h timeline
      this._renderAutopilotTimeline(plan.hourly_plan);

      // Update estimation values
      const setEl = (id, val) => { const el = this.shadowRoot.getElementById(id); if (el) el.textContent = val; };
      setEl('ap-est-net', `${(plan.net_savings || 0).toFixed(2)} zł`);
      setEl('ap-est-selfcons', `${(plan.total_self_consumption_kwh || 0).toFixed(1)} kWh`);
      setEl('ap-est-import', `${(plan.total_import_kwh || 0).toFixed(1)} kWh`);
      setEl('ap-est-export', `${(plan.total_export_kwh || 0).toFixed(1)} kWh`);
      setEl('ap-est-cost', `${(plan.total_cost || 0).toFixed(2)} zł`);
      const vs = plan.vs_no_management || 0;
      setEl('ap-est-vs', `${vs >= 0 ? '+' : ''}${vs.toFixed(2)} zł`);

      // AI Analysis
      if (plan.ai_analysis) {
        const analysisEl = this.shadowRoot.getElementById('ap-ai-analysis');
        if (analysisEl) {
          analysisEl.innerHTML = this._markdownToHtml(plan.ai_analysis);
        }
      }

      // Estimation log entry no longer written to ap-activity-log
      // (live decision log handles real-time actions from coordinator)

    } catch (e) { /* settings not ready yet */ }
  }

  _renderAutopilotTimeline(hourlyPlan) {
    const container = this.shadowRoot.getElementById('ap-timeline');
    if (!container || !hourlyPlan) return;

    const currentHour = new Date().getHours();

    // Find max absolute battery delta for scaling bars
    const maxDelta = Math.max(1, ...hourlyPlan.map(h => Math.abs(h.battery || 0)));

    container.innerHTML = hourlyPlan.map(h => {
      const action = h.action || 'hold';
      const barHeight = Math.max(5, Math.abs(h.battery || 0) / maxDelta * 50);
      const isCurrent = h.hour === currentHour;
      const tooltip = `${h.hour}:00 | ${action.toUpperCase()} | SOC: ${h.soc_start}→${h.soc_end}% | PV: ${h.pv}W | Load: ${h.load}W`;

      return `<div class="ap-th ${action} ${isCurrent ? 'current' : ''}" title="${tooltip}">
        <div class="ap-th-bar" style="height:${barHeight}px"></div>
        <div class="ap-th-label">${h.hour}</div>
      </div>`;
    }).join('');
  }

  _updateAutopilotLog(plan) {
    const logEl = this.shadowRoot.getElementById('ap-activity-log');
    if (!logEl) return;

    const timestamp = plan.timestamp || new Date().toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit' });
    const provider = plan.provider || '—';
    const strategy = plan.label || plan.strategy || '—';
    const net = (plan.net_savings || 0).toFixed(2);

    // Prepend new entry
    const entry = `<div style="display:flex; justify-content:space-between; padding:6px 8px; border-bottom:1px solid rgba(255,255,255,0.04)">
      <span><strong>${timestamp}</strong> ${strategy}</span>
      <span style="color:${parseFloat(net) >= 0 ? '#2ecc71' : '#e74c3c'}">${net} zł</span>
      <span style="color:#64748b; font-size:9px">${provider}</span>
    </div>`;

    const existing = logEl.innerHTML;
    if (existing.includes('Brak aktywności')) {
      logEl.innerHTML = entry;
    } else {
      logEl.innerHTML = entry + existing;
    }
    // Keep max 10 entries
    const entries = logEl.querySelectorAll(':scope > div');
    if (entries.length > 10) {
      for (let i = 10; i < entries.length; i++) entries[i].remove();
    }
  }

  _updateLiveDecisionLog() {
    // Read live autopilot data from settings.json (written by coordinator every tick)
    if (!this._hass) return;

    try {
      fetch('/local/smartinghome/settings.json?t=' + Date.now())
        .then(r => r.json())
        .then(s => {
          // ── Sync strategy state ──
          const saved = s.autopilot_active_strategy;
          if (saved && saved !== this._autopilotActiveStrategy) {
            this._autopilotActiveStrategy = saved;
            this._lastRenderedActionsKey = null; this._renderStrategyPresets(); this._renderActionSections();
          }
          const statusEl = this.shadowRoot.getElementById('ap-status');
          const deactBtn = this.shadowRoot.getElementById('ap-deactivate-btn');
          if (statusEl && saved) {
            statusEl.textContent = '● AKTYWNY';
            statusEl.style.color = '#2ecc71';
            if (deactBtn) deactBtn.style.display = 'inline-block';
          } else if (statusEl && !saved) {
            if (this._autopilotActiveStrategy) {
              this._autopilotActiveStrategy = null;
              this._lastRenderedActionsKey = null; this._renderStrategyPresets(); this._renderActionSections();
            }
            statusEl.textContent = '● GOTOWY';
            statusEl.style.color = '#64748b';
            if (deactBtn) deactBtn.style.display = 'none';
          }

          // ── Live tick status bar ──
          const live = s.autopilot_live;
          if (live) {
            const setText = (id, val) => {
              const el = this.shadowRoot.getElementById(id);
              if (el) el.textContent = val;
            };

            // Frontend fallback labels for strategy keys
            const strategyLabels = {
              max_self_consumption: '🟢 Max Autokonsumpcja',
              max_profit: '💰 Max Zysk',
              battery_protection: '🔋 Ochrona Baterii',
              zero_export: '⚡ Zero Export',
              weather_adaptive: '🌧️ Pogodowy',
              ai_full_autonomy: '🧠 AI Pełna',
            };

            if (live.enabled === false) {
              setText('ap-live-strategy', '— tryb auto');
            } else {
              const label = live.strategy_label || strategyLabels[live.strategy] || live.strategy || '—';
              setText('ap-live-strategy', label);
            }

            // G13 zone with color
            const zoneEl = this.shadowRoot.getElementById('ap-live-zone');
            if (zoneEl) {
              const zoneMap = { off_peak: '🌙 Off-peak', morning_peak: '☀️ Szczyt poranny', afternoon_peak: '⚡ Szczyt popołudniowy' };
              const zoneColors = { off_peak: '#2ecc71', morning_peak: '#e74c3c', afternoon_peak: '#e74c3c' };
              zoneEl.textContent = zoneMap[live.g13_zone] || live.g13_zone || '—';
              zoneEl.style.color = zoneColors[live.g13_zone] || '#f8fafc';
            }

            setText('ap-live-soc', live.soc != null ? `${Math.round(live.soc)}%` : '—');
            setText('ap-live-pv', live.pv != null ? `${Math.round(live.pv)} W` : '—');
            setText('ap-live-load', live.load != null ? `${Math.round(live.load)} W` : '—');
            setText('ap-live-surplus', live.surplus != null ? `${Math.round(live.surplus)} W` : '—');

            // ── Current actions from this tick ──
            const actionsEl = this.shadowRoot.getElementById('ap-live-actions');
            if (actionsEl) {
              const actions = live.actions || [];
              if (actions.length === 0) {
                actionsEl.innerHTML = '<span style="color:#64748b">✅ Brak akcji — system pracuje w trybie auto</span>';
                actionsEl.style.borderLeftColor = '#334155';
              } else {
                actionsEl.innerHTML = actions.map(a => {
                  const isWarning = a.includes('⚠️') || a.includes('emergency');
                  const isAI = a.includes('AI CTRL') || a.includes('AI DRY-RUN');
                  const color = isWarning ? '#f7b731' : isAI ? '#a78bfa' : '#2ecc71';
                  return `<div style="padding:2px 0; color:${color}">→ ${a}</div>`;
                }).join('');
                actionsEl.style.borderLeftColor = actions.some(a => a.includes('AI CTRL')) ? '#7c3aed' : '#2ecc71';
              }
            }

            // ── AI Reasoning display ──
            const aiReasoningWrap = this.shadowRoot.getElementById('ap-ai-reasoning-wrap');
            const aiReasoningEl = this.shadowRoot.getElementById('ap-ai-reasoning');
            if (aiReasoningWrap && aiReasoningEl) {
              if (live.ai_reasoning && live.strategy === 'ai_full_autonomy') {
                aiReasoningWrap.style.display = 'block';
                aiReasoningEl.textContent = live.ai_reasoning;
              } else {
                aiReasoningWrap.style.display = 'none';
              }
            }
          }

          // ── Decision log feed ──
          const log = s.autopilot_decision_log;
          const logEl = this.shadowRoot.getElementById('ap-activity-log');
          if (logEl && log && log.length > 0) {
            logEl.innerHTML = log.slice().reverse().map(entry => {
              const icon = entry.action?.includes('ai_ctrl') ? '🧠' :
                           entry.action?.includes('ai_cmd') ? '🤖' :
                           entry.action?.includes('emergency') ? '🚨' :
                           entry.action?.includes('charge') ? '🔋' :
                           entry.action?.includes('export') ? '⚡' :
                           entry.action?.includes('voltage') ? '🔌' :
                           entry.action?.includes('surplus') ? '☀️' :
                           entry.action?.includes('soc') ? '🔋' : '📌';
              return `<div style="display:flex; justify-content:space-between; padding:4px 8px; border-bottom:1px solid rgba(255,255,255,0.04)">
                <span><strong style="color:#f8fafc">${entry.time || '—'}</strong> ${icon} ${entry.message || entry.action || '—'}</span>
                <span style="color:#475569; font-size:9px">${entry.strategy || ''}</span>
              </div>`;
            }).join('');
          }
        })
        .catch(() => {});
    } catch (e) {}
  }

  _markdownToHtml(md) {
    if (!md) return '';
    return md
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/^### (.+)$/gm, '<h3>$1</h3>')
      .replace(/^## (.+)$/gm, '<h2>$1</h2>')
      .replace(/^# (.+)$/gm, '<h2>$1</h2>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      .replace(/`([^`]+)`/g, '<code style="background:rgba(255,255,255,0.06);padding:1px 4px;border-radius:3px;font-size:10px">$1</code>')
      .replace(/\n\n/g, '<br><br>')
      .replace(/\n/g, '<br>')
      .replace(/\|(.+)\|/g, (match) => {
        const cells = match.split('|').filter(c => c.trim());
        if (cells.every(c => c.trim().match(/^[-:]+$/))) return '';
        const tag = cells.every(c => c.trim().match(/^[-:]+$/)) ? 'th' : 'td';
        return '<tr>' + cells.map(c => `<${tag}>${c.trim()}</${tag}>`).join('') + '</tr>';
      });
  }
}

customElements.define("smartinghome-panel", SmartingHomePanel);
