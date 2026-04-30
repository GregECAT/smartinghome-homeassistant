/**
 * Smarting HOME — Custom Panel for Home Assistant
 * Advanced Power Flow Dashboard (Sunsynk-style)
 * © 2026 Smarting HOME by GregECAT
 */

// ── Provider pricing data (PLN/kWh brutto, energia + przesył) ──
const SH_PROVIDER_PRICES = {
  tauron: {
    label: 'Tauron',
    G11:  { flat: 0.87 },
    G12:  { off_peak: 0.55, peak: 1.10 },
    G12w: { off_peak: 0.55, peak: 1.10 },
    G13:  { off_peak: 0.63, morning: 0.91, peak: 1.50 },
  },
  pge: {
    label: 'PGE',
    G11:   { flat: 1.10 },
    G12:   { off_peak: 0.61, peak: 1.25 },
    G12w:  { off_peak: 0.69, peak: 1.30 },
    G12n:  { off_peak: 0.59, peak: 1.21 },
  },
};

// Tariffs available per provider
const SH_PROVIDER_TARIFFS = {
  tauron: [
    { value: 'G13',  label: 'G13 — trzystrefowa (przedpołudniowa / popołudniowa / off-peak + weekendy)' },
    { value: 'G12w', label: 'G12w — dwustrefowa + weekendy (13-15 + 22-06 + weekendy taniej)' },
    { value: 'G12',  label: 'G12 — dwustrefowa (13-15 + 22-06 taniej)' },
    { value: 'G11',  label: 'G11 — jednostrefowa (stała cena cały czas)' },
    { value: 'Dynamic', label: 'Dynamiczna — cena godzinowa ENTSO-E' },
  ],
  pge: [
    { value: 'G12w', label: 'G12w — dwustrefowa + weekendy + letnia/zimowa' },
    { value: 'G12',  label: 'G12 — dwustrefowa (letnia/zimowa + noc)' },
    { value: 'G12n', label: 'G12n — niedzielna (noc 1-5 + niedziele taniej)' },
    { value: 'G11',  label: 'G11 — jednostrefowa (stała 1.10 zł/kWh)' },
    { value: 'Dynamic', label: 'Dynamiczna — cena godzinowa ENTSO-E' },
  ],
};

// Provider labels for selectbox
const SH_PROVIDER_LABELS = {
  tauron: 'Tauron',
  pge: 'PGE (Polska Grupa Energetyczna)',
};

class SmartingHomePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._activeTab = "overview";
    this._isFullscreen = false;
    this._settings = {};
    // Day/Night energy: now computed server-side in coordinator.py
    // (previously browser-based accumulators — removed)
  }

  set hass(hass) {
    // Graceful reconnect (no page reload)
    if (this._wasDisconnected && hass.connected) {
      console.log('[SmartingHOME] HA reconnected — graceful recovery (no reload)');
      this._wasDisconnected = false;
      this._reloadScheduled = false;
      this._hideReconnectBanner();
      this._hass = hass;
      this._ensureSubscriptions();
      this._updateAll();
      return;
    }
    if (hass.connected === false) {
      if (!this._wasDisconnected) {
        this._wasDisconnected = true;
        console.log('[SmartingHOME] HA connection lost — showing banner, keeping last data');
        this._showReconnectBanner();
      }
      // Don't clear this._hass — keep last known state for cache
      return;
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
    // Day/night energy tracking is now server-side — no browser persistence needed
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
    if (this._roiSub) { try { this._roiSub(); } catch(e) {} this._roiSub = null; }
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
    if (tab === 'wind') { this._initWindTab(); this._loadWindData(); this._fetchWindHistoricalStats(); this._initWindCalendar(); }
    if (tab === 'hems') { this._updateHEMSArbitrage(); }
    if (tab === 'history') { this._updateHistoryTab(); }
    if (tab === 'autopilot') { this._updateAutopilot(); }
    if (tab === 'energy' || tab === 'battery' || tab === 'overview') { this._updateForecastCharts(); }
    if (tab === 'forecast') { this._initForecastTab(); }
    if (tab === 'alerts') { this._updateAlertsTab(); this._loadNotificationConfig(); }
  }

  /* ── Sensor mapping ─────────────────────── */
  static DM = {
    pv_power:"sensor.goodwe_total_power", load_power:"sensor.goodwe_house_consumption",
    grid_power:"sensor.goodwe_meter_active_power_total", battery_power:"sensor.goodwe_battery_power",
    battery_soc:"sensor.goodwe_battery_state_of_charge",
    pv1_power:"sensor.goodwe_pv1_power", pv1_voltage:"sensor.goodwe_pv1_voltage", pv1_current:"sensor.goodwe_pv1_current",
    pv2_power:"sensor.goodwe_pv2_power", pv2_voltage:"sensor.goodwe_pv2_voltage", pv2_current:"sensor.goodwe_pv2_current",
    pv3_power:"", pv4_power:"",
    battery_voltage:"sensor.goodwe_battery_voltage", battery_current:"sensor.goodwe_battery_current",
    battery_temp:"sensor.goodwe_battery_temperature", battery_capacity_kwh:"",
    battery_soh:"sensor.goodwe_battery_state_of_health",
    battery_charge_limit:"sensor.goodwe_battery_charge_limit", battery_discharge_limit:"sensor.goodwe_battery_discharge_limit",
    voltage_l1:"sensor.goodwe_on_grid_l1_voltage", voltage_l2:"sensor.goodwe_on_grid_l2_voltage", voltage_l3:"sensor.goodwe_on_grid_l3_voltage",
    current_l1:"sensor.goodwe_on_grid_l1_current", current_l2:"sensor.goodwe_on_grid_l2_current", current_l3:"sensor.goodwe_on_grid_l3_current",
    power_l1:"sensor.goodwe_active_power_l1", power_l2:"sensor.goodwe_active_power_l2", power_l3:"sensor.goodwe_active_power_l3",
    grid_frequency:"sensor.goodwe_meter_frequency",
    pv_today:"sensor.goodwe_today_s_pv_generation",
    grid_import_today:"sensor.goodwe_today_energy_import", grid_export_today:"sensor.goodwe_today_energy_export",
    battery_charge_today:"sensor.goodwe_today_battery_charge", battery_discharge_today:"sensor.goodwe_today_battery_discharge",
    inverter_power:"sensor.goodwe_active_power", inverter_temp:"sensor.goodwe_inverter_temperature_air",
    inverter_temp_radiator:"sensor.goodwe_inverter_temperature_radiator",
    diag_status_code:"sensor.goodwe_diag_status_code", meter_power_factor:"sensor.goodwe_meter_power_factor",
    backup_load:"sensor.goodwe_back_up_load", ups_load_pct:"sensor.goodwe_ups_load", ems_mode:"sensor.goodwe_ems_mode",
    inverter_model:"",
    weather_temp:"", weather_humidity:"", weather_cloud_cover:"",
    weather_wind_speed:"", weather_pressure:"", weather_uv_index:"",
    local_temp:"", local_humidity:"", local_wind_speed:"", local_rain_rate:"",
    local_solar_radiation:"", local_dewpoint:"", local_uv_index:"", local_solar_lux:"",
    local_pressure:"", local_daily_rain:"", local_wind_direction:"", local_wind_gust:"", local_feels_like:"",
  };
  _m(k) {
    // Check local overrides from entity picker first
    const override = this._sensorMapOverrides?.[k];
    if (override) return override;
    const m = this._hass?.states['sensor.smartinghome_sensor_map']?.attributes;
    return m?.[k] || SmartingHomePanel.DM[k] || '';
  }
  _s(id) { return id && this._hass?.states[id] ? this._hass.states[id].state : null; }
  _n(id) {
    const v = parseFloat(this._s(id));
    if (!isNaN(v)) {
      // Cache fresh sensor value
      if (!this._sensorCache) this._sensorCache = {};
      this._sensorCache[id] = { val: v, ts: Date.now() };
      return v;
    }
    // Fallback: use cached value if < 60s old (prevents "—" during reconnection)
    const cached = this._sensorCache?.[id];
    if (cached && (Date.now() - cached.ts) < 60000) return cached.val;
    return null;
  }
  _f(id, d=1) { const v = this._n(id); return v === null ? "—" : v.toFixed(d); }
  _fm(k, d=1) {
    // For grid daily, prefer SmartingHOME corrected sensor (same as _nm)
    if (k === 'grid_import_today' || k === 'grid_export_today') {
      const correctedKey = k === 'grid_import_today' ? 'grid_import_daily' : 'grid_export_daily';
      const corrected = this._findSmartingHomeSensor?.(correctedKey);
      if (corrected !== null && corrected !== undefined) return corrected.toFixed(d);
    }
    return this._f(this._m(k), d);
  }
  _nm(k) {
    // For grid daily values, prefer SmartingHOME midnight-corrected sensors
    // over raw GoodWe daily sensors (which reset at sunrise, not midnight)
    if (k === 'grid_import_today' || k === 'grid_export_today') {
      const correctedKey = k === 'grid_import_today' ? 'grid_import_daily' : 'grid_export_daily';
      const corrected = this._findSmartingHomeSensor(correctedKey);
      if (corrected !== null) return corrected;
    }
    return this._n(this._m(k));
  }
  _findSmartingHomeSensor(key) {
    // Search for SmartingHOME-generated sensor by key suffix
    if (!this._hass?.states) return null;
    const suffixes = [`smartinghome_${key}`, `_${key}`];
    for (const suf of suffixes) {
      for (const eid of Object.keys(this._hass.states)) {
        if (eid.startsWith("sensor.") && eid.endsWith(suf)) {
          const v = this._n(eid);
          if (v !== null) return v;
        }
      }
    }
    return null;
  }
  _setText(id, val) { const el = this.shadowRoot.getElementById(id); if (el) el.textContent = val; }
  _callService(domain, service, data = {}) { if (this._hass) this._hass.callService(domain, service, data); }

  /* ── Sensor Labels (human-readable, PL) ─────────── */
  static SENSOR_LABELS = {
    pv_power: '☀️ Moc PV (W)', load_power: '🏠 Obciążenie domu (W)',
    grid_power: '🔌 Moc sieci (W)', battery_power: '🔋 Moc baterii (W)',
    battery_soc: '🔋 SOC baterii (%)',
    pv1_power: '☀️ PV1 Moc (W)', pv1_voltage: '☀️ PV1 Napięcie (V)', pv1_current: '☀️ PV1 Prąd (A)',
    pv2_power: '☀️ PV2 Moc (W)', pv2_voltage: '☀️ PV2 Napięcie (V)', pv2_current: '☀️ PV2 Prąd (A)',
    pv3_power: '☀️ PV3 Moc (W)', pv4_power: '☀️ PV4 Moc (W)',
    battery_voltage: '🔋 Napięcie baterii (V)', battery_current: '🔋 Prąd baterii (A)',
    battery_temp: '🌡️ Temperatura baterii (°C)', battery_capacity_kwh: '🔋 Pojemność baterii (kWh)',
    voltage_l1: '⚡ Napięcie L1 (V)', voltage_l2: '⚡ Napięcie L2 (V)', voltage_l3: '⚡ Napięcie L3 (V)',
    current_l1: '⚡ Prąd L1 (A)', current_l2: '⚡ Prąd L2 (A)', current_l3: '⚡ Prąd L3 (A)',
    power_l1: '⚡ Moc L1 (W)', power_l2: '⚡ Moc L2 (W)', power_l3: '⚡ Moc L3 (W)',
    grid_frequency: '⚡ Częstotliwość sieci (Hz)',
    pv_today: '☀️ PV dziś (kWh)', grid_import_today: '↓ Import dziś (kWh)',
    grid_export_today: '↑ Eksport dziś (kWh)', battery_charge_today: '🔋 Ładowanie dziś (kWh)',
    battery_discharge_today: '⚡ Rozładowanie dziś (kWh)',
    inverter_power: '⚙️ Moc falownika (W)', inverter_temp: '🌡️ Temp. falownika (°C)',
    weather_temp: '🌤️ Temp. zewn. (°C)', weather_humidity: '💧 Wilgotność (%)',
    weather_cloud_cover: '☁️ Zachmurzenie (%)', weather_wind_speed: '💨 Wiatr (km/h)',
    weather_pressure: '🌡️ Ciśnienie (hPa)', weather_uv_index: '☀️ UV Index',
  };

  /* ── Entity Picker Modal ────────────────── */
  _openEntityPicker(sensorKey, currentEntityId) {
    if (!this._hass || !this._hass.states) return;
    const label = SmartingHomePanel.SENSOR_LABELS[sensorKey] || sensorKey;
    // Save for callback
    this._pickerSensorKey = sensorKey;

    // Build list of sensor entities
    const entities = Object.keys(this._hass.states)
      .filter(eid => eid.startsWith('sensor.'))
      .sort((a, b) => {
        const na = this._hass.states[a]?.attributes?.friendly_name || a;
        const nb = this._hass.states[b]?.attributes?.friendly_name || b;
        return na.localeCompare(nb);
      });

    const listHtml = entities.map(eid => {
      const fn = this._hass.states[eid]?.attributes?.friendly_name || eid;
      const val = this._hass.states[eid]?.state || '—';
      const unit = this._hass.states[eid]?.attributes?.unit_of_measurement || '';
      const isCurrent = eid === currentEntityId;
      return `<div class="ep-item${isCurrent ? ' ep-current' : ''}" data-eid="${eid}" onclick="this.getRootNode().host._selectEntity('${eid}')">
        <div class="ep-name">${fn}</div>
        <div class="ep-meta"><span class="ep-eid">${eid}</span><span class="ep-val">${val} ${unit}</span></div>
      </div>`;
    }).join('');

    const html = `
      <div class="sh-modal-title">🔌 Wybierz encję — ${label}</div>
      <div class="ep-search-wrap">
        <input class="ep-search" type="text" id="sh-ep-search" placeholder="🔍 Szukaj encji..." oninput="this.getRootNode().host._filterEntityList(this.value)" />
      </div>
      <div class="ep-list" id="sh-ep-list">
        ${listHtml}
      </div>
      <div class="sh-modal-actions">
        ${currentEntityId ? `<button class="sh-modal-btn danger" onclick="this.getRootNode().host._selectEntity('')">✕ Wyczyść</button>` : ''}
        <button class="sh-modal-btn" onclick="this.getRootNode().host._closeModal()">Anuluj</button>
      </div>
    `;
    this._showModal(html);
    setTimeout(() => {
      const inp = this.shadowRoot.getElementById('sh-ep-search');
      if (inp) inp.focus();
    }, 120);
  }

  _filterEntityList(query) {
    const list = this.shadowRoot.getElementById('sh-ep-list');
    if (!list) return;
    const q = query.toLowerCase();
    list.querySelectorAll('.ep-item').forEach(el => {
      const text = (el.querySelector('.ep-name')?.textContent || '') + ' ' + (el.querySelector('.ep-eid')?.textContent || '');
      el.style.display = text.toLowerCase().includes(q) ? '' : 'none';
    });
  }

  _selectEntity(entityId) {
    const key = this._pickerSensorKey;
    if (!key) return;
    // Save via service call
    if (this._hass) {
      this._hass.callService('smartinghome', 'update_sensor_map', {
        sensor_key: key,
        entity_id: entityId,
      });
    }
    // Update local DM override for immediate effect
    if (!this._sensorMapOverrides) this._sensorMapOverrides = {};
    this._sensorMapOverrides[key] = entityId;
    // Also persist to settings.json for frontend restart
    this._savePanelSettings({ sensor_map_overrides: { ...(this._settings.sensor_map_overrides || {}), [key]: entityId } });
    this._closeModal();
    // Refresh display
    if (this._hass) this._updateAll();
  }

  /* ── Enhanced _m() with overrides ────────── */
  _mResolved(k) {
    // Priority: 1) local override from entity picker, 2) HA sensor_map attribute, 3) DM defaults
    const override = this._sensorMapOverrides?.[k];
    if (override) return override;
    const m = this._hass?.states['sensor.smartinghome_sensor_map']?.attributes;
    return m?.[k] || SmartingHomePanel.DM[k] || '';
  }

  /* ── Placeholder-aware value renderer ───── */
  _fmp(sensorKey, decimals = 1) {
    const entityId = this._mResolved(sensorKey);
    // Check if entity_id exists in HA states
    if (!entityId || !this._hass?.states[entityId]) {
      // No entity configured or entity doesn't exist → placeholder
      const label = SmartingHomePanel.SENSOR_LABELS[sensorKey] || sensorKey;
      return `<span class="entity-placeholder" onclick="this.getRootNode().host._openEntityPicker('${sensorKey}', '')" title="Kliknij aby wybrać encję">⚙️ ${label}</span>`;
    }
    const v = this._n(entityId);
    return v === null ? '—' : v.toFixed(decimals);
  }

  /* ── Reconnect Banner ─────────────────────── */
  _showReconnectBanner() {
    let b = this.shadowRoot.getElementById('sh-reconnect-banner');
    if (!b) {
      b = document.createElement('div');
      b.id = 'sh-reconnect-banner';
      b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;background:linear-gradient(90deg,rgba(245,158,11,0.95),rgba(251,191,36,0.95));color:#000;text-align:center;padding:10px 16px;font-size:13px;font-weight:600;backdrop-filter:blur(8px);box-shadow:0 2px 12px rgba(0,0,0,0.3);transition:opacity 0.3s;';
      b.innerHTML = '⏳ Ponowne łączenie z Home Assistant… <span style="font-weight:400;opacity:0.7;margin-left:8px">(dane z cache)</span>';
      this.shadowRoot.appendChild(b);
    }
    b.style.display = 'block';
    b.style.opacity = '1';
  }
  _hideReconnectBanner() {
    const b = this.shadowRoot.getElementById('sh-reconnect-banner');
    if (b) {
      b.style.opacity = '0';
      setTimeout(() => { b.style.display = 'none'; }, 300);
    }
  }

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
        // Day/night energy: computed server-side — no browser restore needed
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
        // Restore energy provider
        const epSel = this.shadowRoot.getElementById('sel-energy-provider');
        if (epSel && this._settings.energy_provider) epSel.value = this._settings.energy_provider;
        // Rebuild tariff options for provider then restore tariff plan
        this._rebuildTariffOptions();
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
        this._updatePricingTable();
        this._renderWeatherForecast();
        // Restore Ecowitt settings
        const ecoChk = this.shadowRoot.getElementById('chk-ecowitt-enabled');
        if (ecoChk && this._settings.ecowitt_enabled !== undefined) ecoChk.checked = this._settings.ecowitt_enabled;
        if (this._settings.ecowitt_enabled) this._detectEcowittSensors();
        // Restore sensor map overrides from entity picker
        if (this._settings.sensor_map_overrides) {
          this._sensorMapOverrides = { ...this._settings.sensor_map_overrides };
        }
        // Restore Peak Sell slider
        const peakSellSlider = this.shadowRoot.getElementById('ap-peak-sell-slider');
        if (peakSellSlider && this._settings.peak_sell_soc_percent !== undefined) {
          peakSellSlider.value = this._settings.peak_sell_soc_percent;
          this._onPeakSellSliderChange(this._settings.peak_sell_soc_percent);
        }
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
        const pvParentLabel = pvLabels[`pv${i}`] || `PV${i}`;

        sc.substrings.forEach((sub, si) => {
          const ratio = ratios ? ratios[si] : (1 / sc.substrings.length);
          const subPower = parentPower * ratio;
          const subVoltage = parentVoltage;
          const subCurrent = parentCurrent * ratio;
          const subKwh = stringKwh * ratio;

          // Independent per-substring label: check pv_labels["pv{i}_sub{si}"] first
          const subLabelKey = `pv${i}_sub${si}`;
          const subLabel = pvLabels[subLabelKey] || `${pvParentLabel} — ${dirLabels[sub.direction] || sub.direction}`;

          const box = document.createElement('div');
          box.className = 'pv-string pv-substring-box';
          box.dataset.parentIdx = i;
          box.innerHTML = `
            <div class="pv-name" style="cursor:pointer" onclick="this.getRootNode().host._editSubstringLabel(${i}, ${si})" title="Kliknij aby zmienić nazwę podstringa"><span id="pv${i}-sub${si}-label-text">${subLabel}</span>
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

  _editSubstringLabel(pvIdx, subIdx) {
    const pvLabels = this._settings.pv_labels || {};
    const dirLabels = { 'N': 'Pn', 'NE': 'PnE', 'E': 'Wsch', 'SE': 'PdE', 'S': 'Pd', 'SW': 'PdZ', 'W': 'Zach', 'NW': 'PnZ' };
    const cfg = this._settings.pv_string_config || {};
    const sc = cfg[`pv${pvIdx}`];
    const sub = sc?.substrings?.[subIdx];
    const pvParentLabel = pvLabels[`pv${pvIdx}`] || `PV${pvIdx}`;
    const subLabelKey = `pv${pvIdx}_sub${subIdx}`;
    const current = pvLabels[subLabelKey] || `${pvParentLabel} — ${dirLabels[sub?.direction] || sub?.direction || ''}`;
    const dirHint = sub?.direction ? ` (${dirLabels[sub.direction] || sub.direction})` : '';
    const html = `
      <div class="sh-modal-title">✏️ Zmień nazwę podstringa PV${pvIdx}.${subIdx + 1}${dirHint}</div>
      <div class="sh-modal-label">Etykieta podstringa</div>
      <input class="sh-modal-input" type="text" id="sh-input-sub-label" value="${current.replace(/"/g, '&quot;')}" />
      <div style="font-size:10px; color:#64748b; margin-top:4px;">Możesz nadać każdemu podstringowi osobną nazwę, niezależną od stringa nadrzędnego.</div>
      <div class="sh-modal-actions">
        <button class="sh-modal-btn" onclick="this.getRootNode().host._closeModal()">Anuluj</button>
        <button class="sh-modal-btn primary" onclick="this.getRootNode().host._saveSubstringLabel(${pvIdx}, ${subIdx})">OK</button>
      </div>
    `;
    this._showModal(html);
    setTimeout(() => {
      const inp = this.shadowRoot.getElementById('sh-input-sub-label');
      if (inp) {
        inp.focus();
        inp.select();
        inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') this._saveSubstringLabel(pvIdx, subIdx); });
      }
    }, 120);
  }

  _saveSubstringLabel(pvIdx, subIdx) {
    const inp = this.shadowRoot.getElementById('sh-input-sub-label');
    if (!inp) return;
    const newLabel = inp.value.trim();
    if (newLabel) {
      const labels = this._settings.pv_labels || {};
      labels[`pv${pvIdx}_sub${subIdx}`] = newLabel;
      this._savePanelSettings({ pv_labels: labels });
      // Update the sub-string label in the DOM
      const el = this.shadowRoot.getElementById(`pv${pvIdx}-sub${subIdx}-label-text`);
      if (el) el.textContent = newLabel;
    }
    this._closeModal();
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

  // ═══════ ROI SIMULATION ENGINE ═══════

  // Installation presets for ROI estimation
  static get INSTALLATION_PRESETS() {
    return {
      real: {
        key: 'real', label: '📊 Moja instalacja',
        desc: 'Realne dane z czujników Home Assistant',
        yearlyPvKwh: null, batCapKwh: null, batMaxRate: null, investZl: null,
      },
      '5kw': {
        key: '5kw', label: '☀️ 5 kW PV + 5 kWh bat',
        desc: 'Mała instalacja domowa — typowy dom 80-120 m², zużycie ~4000 kWh/rok',
        yearlyPvKwh: 5000, batCapKwh: 5.0, batMaxRate: 2.5, investZl: 35000,
      },
      '10kw': {
        key: '10kw', label: '☀️ 10 kW PV + 10 kWh bat',
        desc: 'Średnia instalacja — dom 120-200 m², pompa ciepła, zużycie ~6500 kWh/rok',
        yearlyPvKwh: 10000, batCapKwh: 10.2, batMaxRate: 3.0, investZl: 55000,
      },
      '15kw': {
        key: '15kw', label: '☀️ 15 kW PV + 15 kWh bat',
        desc: 'Duża instalacja z pełnym HEMS — dom z klimatyzacją + EV, zużycie ~10000 kWh/rok',
        yearlyPvKwh: 15000, batCapKwh: 15.0, batMaxRate: 5.0, investZl: 75000,
      },
      custom: {
        key: 'custom', label: '🔧 Niestandardowa',
        desc: 'Własne parametry PV, magazynu i mocy',
        yearlyPvKwh: null, batCapKwh: null, batMaxRate: null, investZl: null,
      },
    };
  }

  _onRoiProfileChange(key) {
    this._roiProfile = key;
    this._savePanelSettings({ roi_profile: key });

    // Show/hide custom panel
    const panel = this.shadowRoot.getElementById('roi-custom-panel');
    if (panel) panel.style.display = key === 'custom' ? 'block' : 'none';

    // Auto-fill investment from preset
    const preset = this.constructor.INSTALLATION_PRESETS[key];
    if (preset?.investZl) {
      const inp = this.shadowRoot.getElementById('roi-invest-input');
      if (inp) inp.value = preset.investZl;
      this._saveRoiInvestment(preset.investZl);
    }

    // Update description
    const desc = this.shadowRoot.getElementById('roi-profile-desc');
    if (desc && preset) desc.textContent = preset.desc;

    // Re-run ROI calculation
    this._updateRoi();
  }

  _onRoiCustomChange() {
    this._roiProfile = 'custom';
    this._updateRoi();
  }

  _getRoiProfileParams() {
    const key = this._roiProfile || 'real';
    const presets = this.constructor.INSTALLATION_PRESETS;
    const preset = presets[key] || presets.real;

    if (key === 'custom') {
      const pvKwp = parseFloat(this.shadowRoot.getElementById('roi-custom-pv')?.value) || 10;
      const yieldKwh = parseFloat(this.shadowRoot.getElementById('roi-custom-yield')?.value) || 1000;
      return {
        yearlyPvKwh: pvKwp * yieldKwh,
        batCapKwh: parseFloat(this.shadowRoot.getElementById('roi-custom-bat')?.value) || 10,
        batMaxRate: parseFloat(this.shadowRoot.getElementById('roi-custom-rate')?.value) || 3,
      };
    }

    return {
      yearlyPvKwh: preset.yearlyPvKwh,
      batCapKwh: preset.batCapKwh,
      batMaxRate: preset.batMaxRate,
    };
  }

  /**
   * Build a typical 24h energy profile from yearly totals.
   * PV: Gaussian bell curve peaking at 12:00.
   * Load: bimodal pattern (morning 7-9, evening 17-21) + baseload.
   * Returns daily kWh arrays scaled so sum × 365 ≈ yearly totals.
   */
  _buildDayProfile(yearlyPV, yearlyLoad) {
    // PV bell curve: Gaussian centered at hour 12, σ=3
    const pvRaw = Array.from({length: 24}, (_, h) => {
      const x = (h - 12) / 3;
      return Math.exp(-0.5 * x * x);
    });
    const pvSum = pvRaw.reduce((a, b) => a + b, 0);
    const dailyPV = yearlyPV / 365;
    const pv = pvRaw.map(v => (v / pvSum) * dailyPV);

    // Load: bimodal (morning peak 7-9, evening peak 17-21) + baseload
    const loadShape = [
      0.25, 0.22, 0.20, 0.20, 0.22, 0.28, // 0-5: nighttime low
      0.40, 0.65, 0.70, 0.55, 0.45, 0.40, // 6-11: morning ramp + peak
      0.42, 0.50, 0.48, 0.50, 0.60, 0.75, // 12-17: midday + evening ramp
      0.85, 0.80, 0.70, 0.55, 0.40, 0.30  // 18-23: evening peak + decline
    ];
    const loadSum = loadShape.reduce((a, b) => a + b, 0);
    const dailyLoad = yearlyLoad / 365;
    const load = loadShape.map(v => (v / loadSum) * dailyLoad);

    return { pv, load };
  }

  /**
   * Build tariff scenarios with hourly buy/sell prices and battery strategy.
   * Uses live ENTSO-E prices_today when available for dynamic tariff.
   */
  _buildTariffScenarios() {
    const now = new Date();
    const month = now.getMonth();
    const isWinter = [0,1,2,9,10,11].includes(month);
    const isSummer = !isWinter;

    // Read ENTSO-E prices_today (24h array) from sensor attributes
    let entsoeToday = [];
    try {
      const st = this._hass?.states?.['sensor.entso_e_aktualna_cena_energii'];
      if (st?.attributes?.prices_today) {
        entsoeToday = st.attributes.prices_today.map(p =>
          typeof p === 'object' ? (p.price || p.value || 0) : (parseFloat(p) || 0)
        );
      }
    } catch(e) {}
    const avgEntso = parseFloat(this._hass?.states?.['sensor.entso_e_srednia_dzisiaj']?.state) || 0.50;

    // G13: ALWAYS compute weekday pricing for main simulation
    // Base price: 0.63 zł brutto (off-peak), morning: 0.85, peak: 1.20
    // Weighted avg weekday: ~0.80 zł (zimowe miesiące podnoszą średnią)
    // Weekend: flat 0.63 zł (like off-peak)
    const g13WeekdayPrice = (h) => {
      if (h >= 7 && h < 13) return 0.85; // morning semi-peak (6h)
      if (isSummer) {
        if (h >= 19 && h < 22) return 1.20; // summer afternoon peak (3h)
        return 0.63; // off-peak (15h)
      } else {
        if (h >= 16 && h < 21) return 1.20; // winter afternoon peak (5h)
        return 0.63; // off-peak (13h)
      }
    };
    const g13WeekendPrice = () => 0.63; // flat off-peak

    // Calculate G13 weighted average for description
    const g13wdPrices = Array.from({length: 24}, (_, h) => g13WeekdayPrice(h));
    const g13wdAvg = (g13wdPrices.reduce((a,b) => a+b, 0) / 24).toFixed(2);
    const g13totalAvg = ((g13wdPrices.reduce((a,b) => a+b, 0) / 24 * 261 + 0.63 * 104) / 365).toFixed(2);

    return [
      {
        key: 'g11',
        label: '🔴 G11 — Stała cena',
        desc: 'Taryfa G11: stała cena 1.10 zł/kWh, eksport po średniej RCE (0.20 zł)',
        color: '#e74c3c',
        border: 'rgba(231,76,60,0.2)',
        bg: 'rgba(231,76,60,0.06)',
        badge: '',
        buyPrice: Array(24).fill(1.10),
        sellPrice: Array(24).fill(0.20),
        strategy: 'passive',
        gridChargeAllowed: false,
        annualDays: 365, // same every day
      },
      {
        key: 'g13',
        label: '🟡 G13 — Strefowa',
        desc: `Taryfa G13: off-peak 0.63, poranna 0.85, szczyt 1.20 zł · śr. weekday ${g13wdAvg} zł · śr. rok ${g13totalAvg} zł (261 dn. rob. + 104 dn. week.)`,
        color: '#f7b731',
        border: 'rgba(247,183,49,0.2)',
        bg: 'rgba(247,183,49,0.06)',
        badge: '',
        buyPrice: g13wdPrices,
        sellPrice: g13wdPrices.map(p => p * 0.35),
        strategy: 'g13_active',
        gridChargeAllowed: false,
        annualDays: 261, // weekdays only
        // Weekend variant for weighted calculation
        weekendBuyPrice: Array(24).fill(0.63),
        weekendSellPrice: Array(24).fill(0.63 * 0.35),
        weekendDays: 104,
      },
      {
        key: 'dynamic',
        label: '🟢 Dynamiczna — RCE/ENTSO-E',
        desc: 'Cena dynamiczna RCE: profil roczny (śr. ' + avgEntso.toFixed(2) + ' zł) — ładuj tanio, sprzedawaj drogo',
        color: '#2ecc71',
        border: 'rgba(46,204,113,0.3)',
        bg: 'rgba(46,204,113,0.08)',
        badge: '',
        // ROI ALWAYS uses annual-average RCE profile (never today's snapshot!)
        // Today's prices may have duck-curve / extreme midday cheapness
        // which is not representative of a full year.
        buyPrice: (() => {
          const rceShape = [
            0.30, 0.25, 0.20, 0.18, 0.15, 0.20, // 0-5: night (cheapest)
            0.35, 0.55, 0.65, 0.60, 0.55, 0.50, // 6-11: morning ramp + midday
            0.45, 0.48, 0.55, 0.65, 0.80, 0.95, // 12-17: afternoon ramp to peak
            1.10, 1.00, 0.85, 0.70, 0.50, 0.35  // 18-23: evening peak + decline
          ];
          const shapeAvg = rceShape.reduce((a,b) => a+b, 0) / 24;
          return rceShape.map(s => Math.max(0.05, s * avgEntso / shapeAvg));
        })(),
        sellPrice: (() => {
          const rceShape = [
            0.30, 0.25, 0.20, 0.18, 0.15, 0.20,
            0.35, 0.55, 0.65, 0.60, 0.55, 0.50,
            0.45, 0.48, 0.55, 0.65, 0.80, 0.95,
            1.10, 1.00, 0.85, 0.70, 0.50, 0.35
          ];
          const shapeAvg = rceShape.reduce((a,b) => a+b, 0) / 24;
          return rceShape.map(s => Math.max(0.02, s * avgEntso / shapeAvg * 0.85));
        })(),
        // Store today's live prices separately (for "today vs annual" display)
        liveBuyPrice: entsoeToday.length >= 24 ? entsoeToday.slice(0, 24) : null,
        liveSellPrice: entsoeToday.length >= 24
          ? entsoeToday.slice(0, 24).map(p => Math.max(0, p * 0.85)) : null,
        strategy: 'dynamic_active',
        gridChargeAllowed: true,
        annualDays: 365,
      },
    ];
  }

  /**
   * Simulate battery operation given a profile and tariff scenario.
   * Runs 2 passes (warmup + measurement) for steady-state SOC accuracy.
   * Tracks energy sources: PV self-consumption, battery-from-PV, battery-from-grid,
   * grid-direct, and battery-to-grid export (arbitrage).
   * Returns day-2 (steady-state) summary KPIs.
   */
  _simulateBatteryDay(profile, scenario, batParams = {}) {
    const CAP = batParams.capKwh || 10.2;
    const MAX_RATE = batParams.maxRate || 3.0;
    const ETA = 0.96;         // one-way efficiency (round-trip ~0.92)
    const SOC_MIN = 0.05;     // 5%
    const SOC_MAX = 1.0;      // 100%

    // Initial battery energy source: passive → assume PV origin, grid-charge → grid origin
    let soc = 0.20;
    let batPvEnergy = scenario.gridChargeAllowed ? 0 : CAP * soc;
    let batGridEnergy = scenario.gridChargeAllowed ? CAP * soc : 0;

    // Pre-compute cheap/expensive hours for strategies
    let cheapHours = [], expensiveHours = [];
    let avgCheapBuy = 0, avgExpensiveSell = 0;
    if (scenario.strategy === 'dynamic_active') {
      const indexed = scenario.buyPrice.map((p, i) => ({p, i}));
      const sorted = [...indexed].sort((a, b) => a.p - b.p);
      const candidateCheap = sorted.slice(0, 8);
      const candidateExpensive = sorted.slice(-8);

      // Calculate potential sell prices for expensive hours
      const expSellPrices = candidateExpensive.map(x => scenario.sellPrice[x.i]);
      const avgExpSell = expSellPrices.reduce((a, b) => a + b, 0) / expSellPrices.length;

      // Profitability filter: only charge if buy_price / efficiency < avg_expensive_sell * threshold
      // Round-trip efficiency ≈ 90% → need sell > buy / 0.9
      // Add margin of 0.05 PLN to cover degradation and real-world losses
      const RT_EFF = ETA * ETA; // round-trip = charge_eff * discharge_eff
      const MIN_SPREAD = 0.08; // minimum spread in PLN/kWh to justify a cycle
      
      for (const c of candidateCheap) {
        const breakEvenSell = c.p / RT_EFF + MIN_SPREAD;
        if (breakEvenSell < avgExpSell) {
          cheapHours.push(c.i);
        }
      }
      for (const e of candidateExpensive) {
        const breakEvenBuy = e.p * RT_EFF - MIN_SPREAD;
        // Only sell if there exist cheap hours that make this profitable
        if (cheapHours.length > 0) {
          const avgCheapPrice = candidateCheap.filter(c => cheapHours.includes(c.i))
            .reduce((a, c) => a + c.p, 0) / Math.max(1, cheapHours.length);
          if (scenario.sellPrice[e.i] > avgCheapPrice / RT_EFF + MIN_SPREAD) {
            expensiveHours.push(e.i);
          }
        }
      }

      // Limit to max 5 charge/discharge hours (≈200 cycles/year max)
      cheapHours = cheapHours.slice(0, 5);
      expensiveHours = expensiveHours.slice(0, 5);

      avgCheapBuy = cheapHours.length > 0
        ? cheapHours.reduce((a, h) => a + scenario.buyPrice[h], 0) / cheapHours.length
        : 0;
      avgExpensiveSell = expensiveHours.length > 0
        ? expensiveHours.reduce((a, h) => a + scenario.sellPrice[h], 0) / expensiveHours.length
        : 0;
    }

    const avgBuy = scenario.buyPrice.reduce((a,b) => a+b, 0) / 24;

    // Run 2 passes: pass 1 = warmup (establishes steady-state SOC), pass 2 = measurement
    let result = null;
    for (let pass = 0; pass < 2; pass++) {
      const gridImport = new Array(24).fill(0);
      const gridExport = new Array(24).fill(0);
      const battSOC = new Array(24).fill(0);
      const batCharge = new Array(24).fill(0);
      const batDischarge = new Array(24).fill(0);
      const pvDirectToLoad = new Array(24).fill(0);
      const batPvToLoad = new Array(24).fill(0);
      const batGridToLoad = new Array(24).fill(0);
      const gridDirectToLoad = new Array(24).fill(0);
      const batToGrid = new Array(24).fill(0);
      const pvToGrid = new Array(24).fill(0);

      for (let h = 0; h < 24; h++) {
        const pvH = profile.pv[h];
        const loadH = profile.load[h];

        // Step 1: PV covers load directly
        const pvToLoadH = Math.min(pvH, loadH);
        pvDirectToLoad[h] = pvToLoadH;
        let remainingLoad = loadH - pvToLoadH;
        let pvSurplus = pvH - pvToLoadH;

        // Step 2: Battery charging from PV surplus
        let chargeFromPV = 0;
        const freeCapKwh = (SOC_MAX - soc) * CAP;

        if (scenario.strategy === 'g13_active') {
          chargeFromPV = Math.min(pvSurplus, MAX_RATE, freeCapKwh / ETA);
        } else if (scenario.strategy === 'dynamic_active') {
          if (expensiveHours.includes(h) && scenario.sellPrice[h] > avgBuy * 0.8) {
            chargeFromPV = 0;
          } else {
            chargeFromPV = Math.min(pvSurplus, MAX_RATE, freeCapKwh / ETA);
          }
        } else {
          chargeFromPV = Math.min(pvSurplus, MAX_RATE, freeCapKwh / ETA);
        }

        const actualPvCharge = chargeFromPV * ETA;
        soc += actualPvCharge / CAP;
        batPvEnergy += actualPvCharge;
        batCharge[h] += chargeFromPV;
        pvSurplus -= chargeFromPV;

        // Step 3: Export remaining PV surplus to grid
        pvToGrid[h] = pvSurplus;
        gridExport[h] = pvSurplus;

        // Step 4: Battery discharge to cover remaining load
        let dischargeToLoad = 0;
        const availableKwh = (soc - SOC_MIN) * CAP;

        if (scenario.strategy === 'g13_active') {
          const price = scenario.buyPrice[h];
          if (price >= 1.20) {
            dischargeToLoad = Math.min(remainingLoad, MAX_RATE, availableKwh * ETA);
          } else if (price >= 0.80) {
            dischargeToLoad = Math.min(remainingLoad * 0.5, MAX_RATE, availableKwh * ETA);
          }
        } else if (scenario.strategy === 'dynamic_active') {
          if (expensiveHours.includes(h)) {
            dischargeToLoad = Math.min(remainingLoad, MAX_RATE, availableKwh * ETA);
          } else if (!cheapHours.includes(h)) {
            dischargeToLoad = Math.min(remainingLoad * 0.5, MAX_RATE, availableKwh * ETA);
          }
        } else {
          dischargeToLoad = Math.min(remainingLoad, MAX_RATE, availableKwh * ETA);
        }

        // Track PV vs grid portion of battery discharge
        const batTotal = batPvEnergy + batGridEnergy;
        const pvRatio = batTotal > 0 ? batPvEnergy / batTotal : (scenario.gridChargeAllowed ? 0 : 1);
        const dischargePv = dischargeToLoad * pvRatio;
        const dischargeGrid = dischargeToLoad * (1 - pvRatio);
        batPvToLoad[h] = dischargePv;
        batGridToLoad[h] = dischargeGrid;

        soc -= dischargeToLoad / (CAP * ETA);
        batPvEnergy = Math.max(0, batPvEnergy - dischargePv);
        batGridEnergy = Math.max(0, batGridEnergy - dischargeGrid);
        batDischarge[h] += dischargeToLoad;
        remainingLoad -= dischargeToLoad;

        // Step 5: Battery-to-grid export (ARBITRAGE — dynamic only)
        if (scenario.strategy === 'dynamic_active' && expensiveHours.includes(h)) {
          const availAfterLoad = (soc - SOC_MIN) * CAP;
          const remainingRate = MAX_RATE - dischargeToLoad;
          // Profitability check: sell price must cover buy cost / RT efficiency + spread margin
          const minSellForProfit = avgCheapBuy > 0 ? avgCheapBuy / (ETA * ETA) + 0.08 : avgBuy * 1.3;
          if (remainingRate > 0.1 && availAfterLoad > 0.5 && scenario.sellPrice[h] > minSellForProfit) {
            const batExport = Math.min(remainingRate, availAfterLoad * ETA, MAX_RATE * 0.7);
            const batExpPv = batExport * pvRatio;
            const batExpGrid = batExport * (1 - pvRatio);
            batToGrid[h] = batExport;
            gridExport[h] += batExport;
            soc -= batExport / (CAP * ETA);
            batPvEnergy = Math.max(0, batPvEnergy - batExpPv);
            batGridEnergy = Math.max(0, batGridEnergy - batExpGrid);
            batDischarge[h] += batExport;
          }
        }

        // Step 6: Grid import for remaining load
        gridDirectToLoad[h] = remainingLoad;
        gridImport[h] = remainingLoad;

        // Step 7: Grid-to-battery charging (dynamic only, cheap hours)
        if (scenario.gridChargeAllowed && cheapHours.includes(h)) {
          const gFree = (SOC_MAX - soc) * CAP;
          const gridCharge = Math.min(MAX_RATE - chargeFromPV, gFree / ETA, MAX_RATE * 0.9);
          if (gridCharge > 0.1) {
            const actualGridCharge = gridCharge * ETA;
            soc += actualGridCharge / CAP;
            batGridEnergy += actualGridCharge;
            gridImport[h] += gridCharge;
            batCharge[h] += gridCharge;
          }
        }

        soc = Math.max(SOC_MIN, Math.min(SOC_MAX, soc));
        battSOC[h] = soc;
      }

      // Only compute aggregates on measurement pass (pass 1)
      if (pass === 1) {
        const totalImport = gridImport.reduce((a, b) => a + b, 0);
        const totalExport = gridExport.reduce((a, b) => a + b, 0);
        const totalCharge = batCharge.reduce((a, b) => a + b, 0);
        const totalDischarge = batDischarge.reduce((a, b) => a + b, 0);

        const totalPvDirectToLoad = pvDirectToLoad.reduce((a, b) => a + b, 0);
        const totalBatPvToLoad = batPvToLoad.reduce((a, b) => a + b, 0);
        const totalBatGridToLoad = batGridToLoad.reduce((a, b) => a + b, 0);
        const totalGridDirectToLoad = gridDirectToLoad.reduce((a, b) => a + b, 0);
        const totalBatToGrid = batToGrid.reduce((a, b) => a + b, 0);
        const totalPvToGrid = pvToGrid.reduce((a, b) => a + b, 0);

        const pvSelfConsumption = totalPvDirectToLoad + totalBatPvToLoad;
        const totalLocalServed = pvSelfConsumption + totalBatGridToLoad;

        let importCost = 0, exportRevenue = 0;
        let arbitrageSellRevenue = 0, arbitrageBuyCost = 0;
        for (let h = 0; h < 24; h++) {
          importCost += gridImport[h] * scenario.buyPrice[h];
          exportRevenue += gridExport[h] * scenario.sellPrice[h];
          arbitrageSellRevenue += batToGrid[h] * scenario.sellPrice[h];
        }
        for (let h = 0; h < 24; h++) {
          if (scenario.gridChargeAllowed && cheapHours.includes(h)) {
            const gc = batCharge[h] - Math.min(profile.pv[h], batCharge[h]);
            if (gc > 0) arbitrageBuyCost += gc * scenario.buyPrice[h];
          }
        }
        const arbitrageProfit = arbitrageSellRevenue - arbitrageBuyCost;

        const avgTariffPrice = scenario.buyPrice.reduce((a,b) => a+b, 0) / 24;
        const dailyLoad = profile.load.reduce((a, b) => a + b, 0);
        const dailyPV = profile.pv.reduce((a, b) => a + b, 0);
        const baselineCost = dailyLoad * avgTariffPrice;

        const systemNetCost = importCost - exportRevenue;
        const dailyBenefit = baselineCost - systemNetCost;

        const avgBuyPrice = totalImport > 0 ? importCost / totalImport : 0;
        const avgSellPrice = totalExport > 0 ? exportRevenue / totalExport : 0;
        const cycles = totalCharge / CAP;

        // Breakdown: import to home vs import to battery (arbitrage)
        const gridToCharge = totalImport - totalGridDirectToLoad;
        const gridToLoad = totalGridDirectToLoad;

        // Battery losses = energy charged - energy discharged usable
        // Charge loss: input * (1-ETA), Discharge loss: output * (1-ETA)/ETA
        const batteryLosses = totalCharge > 0
          ? totalCharge * (1 - ETA) + totalDischarge * (1 - ETA) / ETA
          : 0;

        // PV-weighted average price: what price would you pay for energy
        // during the hours PV actually produces (production-weighted)
        let pvWeightedPriceSum = 0;
        let pvWeightedPriceTotal = 0;
        for (let h = 0; h < 24; h++) {
          if (profile.pv[h] > 0) {
            pvWeightedPriceSum += profile.pv[h] * scenario.buyPrice[h];
            pvWeightedPriceTotal += profile.pv[h];
          }
        }
        const avgPricePvHours = pvWeightedPriceTotal > 0
          ? pvWeightedPriceSum / pvWeightedPriceTotal
          : avgTariffPrice;

        // Proper PV savings = self-consumed PV × price during PV hours
        const pvSavings = pvSelfConsumption * avgPricePvHours;

        // PV export revenue (only PV→grid, not battery→grid)
        let pvExportRevenue = 0;
        for (let h = 0; h < 24; h++) {
          pvExportRevenue += (pvToGrid[h] || 0) * scenario.sellPrice[h];
        }

        // Effective value of 1 kWh PV = (PV savings + PV export revenue) / total PV
        // This excludes battery arbitrage profit which is not PV-attributable
        const effectivePvValue = dailyPV > 0
          ? (pvSavings + pvExportRevenue) / dailyPV
          : 0;

        result = {
          gridImport, gridExport, battSOC, batCharge, batDischarge,
          totalImport, totalExport, totalCharge, totalDischarge,
          pvSelfConsumption, totalBatPvToLoad, totalBatGridToLoad,
          totalGridDirectToLoad, totalLocalServed,
          totalBatToGrid, totalPvToGrid, totalPvDirectToLoad,
          gridToCharge, gridToLoad, batteryLosses, dailyPV, dailyLoad,
          avgPricePvHours, pvSavings, pvExportRevenue, effectivePvValue,
          importCost, exportRevenue, baselineCost, systemNetCost, dailyBenefit,
          avgBuyPrice, avgSellPrice, cycles,
          arbitrageProfit, arbitrageSellRevenue, arbitrageBuyCost,
        };
      }
    }
    return result;
  }

  // ═══════ END ROI SIMULATION ENGINE ═══════

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

    // ROI calculation — per-tariff simulation
    let invest = this._settings.roi_investment || 0;
    const invInput = this.shadowRoot.getElementById("roi-invest-input");
    if (invInput && !invInput.matches(":focus") && invest) invInput.value = invest;

    // Restore profile selector state
    const profileKey = this._roiProfile || this._settings.roi_profile || 'real';
    this._roiProfile = profileKey;
    const profileSelect = this.shadowRoot.getElementById('roi-profile-select');
    if (profileSelect && profileSelect.value !== profileKey) profileSelect.value = profileKey;
    const customPanel = this.shadowRoot.getElementById('roi-custom-panel');
    if (customPanel) customPanel.style.display = profileKey === 'custom' ? 'block' : 'none';
    const profileDesc = this.shadowRoot.getElementById('roi-profile-desc');
    const presetObj = this.constructor.INSTALLATION_PRESETS[profileKey];
    if (profileDesc && presetObj) profileDesc.textContent = presetObj.desc;

    // Get profile parameters (may override PV + battery)
    const profileParams = this._getRoiProfileParams();

    // Estimate yearly energy values from current period
    const multiplier = { day: 365, week: 52, month: 12, year: 1 };
    const sensorYearlyPV = pvVal * multiplier[p];
    const yearlyImport = impVal * multiplier[p];
    const yearlyExport = expVal * multiplier[p];
    const yearlySelfUse = selfUse * multiplier[p];

    // Use profile PV if preset, otherwise sensor data
    const yearlyPV = profileParams.yearlyPvKwh || sensorYearlyPV;

    // Build battery params from profile
    const batParams = {};
    if (profileParams.batCapKwh) batParams.capKwh = profileParams.batCapKwh;
    if (profileParams.batMaxRate) batParams.maxRate = profileParams.batMaxRate;

    // Label — show source
    const periodLabels = { day: "dzień", week: "tydzień", month: "miesiąc", year: "rok" };
    const srcLabel = profileKey === 'real'
      ? `Baza: ${periodLabels[p]} × ${multiplier[p]}`
      : `Baza: profil ${presetObj?.label || profileKey}`;
    this._setText("roi-period-label", srcLabel);

    // Build per-tariff simulation
    const tariffScenarios = this._buildTariffScenarios();
    const yearlyLoad = yearlySelfUse + yearlyImport;
    const ct = this.shadowRoot.getElementById("roi-scenario-cards");

    if (ct && yearlyPV > 0 && yearlyLoad > 0) {
      const profile = this._buildDayProfile(yearlyPV, yearlyLoad);

      const results = tariffScenarios.map(sc => {
        const sim = this._simulateBatteryDay(profile, sc, batParams);
        const days = sc.annualDays || 365;

        // For G13: also simulate weekend and weight results
        let wkSim = null;
        if (sc.weekendBuyPrice) {
          const weekendSc = { ...sc, buyPrice: sc.weekendBuyPrice, sellPrice: sc.weekendSellPrice };
          wkSim = this._simulateBatteryDay(profile, weekendSc, batParams);
        }
        const wkDays = sc.weekendDays || 0;
        const totalDays = days + wkDays;

        // Helper: weighted daily value → annual
        const wa = (main, weekend) => wkSim
          ? (main * days + weekend * wkDays)
          : (main * totalDays);

        // Scale daily results to yearly (weighted for G13)
        const yr = {
          import: wa(sim.totalImport, wkSim?.totalImport || 0),
          export: wa(sim.totalExport, wkSim?.totalExport || 0),
          pvSelfCons: wa(sim.pvSelfConsumption, wkSim?.pvSelfConsumption || 0),
          pvDirect: wa(sim.totalPvDirectToLoad, wkSim?.totalPvDirectToLoad || 0),
          batPvToLoad: wa(sim.totalBatPvToLoad, wkSim?.totalBatPvToLoad || 0),
          batGridToLoad: wa(sim.totalBatGridToLoad, wkSim?.totalBatGridToLoad || 0),
          gridDirect: wa(sim.totalGridDirectToLoad, wkSim?.totalGridDirectToLoad || 0),
          batToGrid: wa(sim.totalBatToGrid, wkSim?.totalBatToGrid || 0),
          pvToGrid: wa(sim.totalPvToGrid, wkSim?.totalPvToGrid || 0),
          localServed: wa(sim.totalLocalServed, wkSim?.totalLocalServed || 0),
          gridToCharge: wa(sim.gridToCharge, wkSim?.gridToCharge || 0),
          gridToLoad: wa(sim.gridToLoad, wkSim?.gridToLoad || 0),
          batteryLosses: wa(sim.batteryLosses, wkSim?.batteryLosses || 0),
          load: wa(sim.dailyLoad, wkSim?.dailyLoad || 0),
          pvTotal: wa(sim.dailyPV, wkSim?.dailyPV || 0),
          importCost: wa(sim.importCost, wkSim?.importCost || 0),
          exportRev: wa(sim.exportRevenue, wkSim?.exportRevenue || 0),
          baselineCost: wa(sim.baselineCost, wkSim?.baselineCost || 0),
          netCost: wa(sim.systemNetCost, wkSim?.systemNetCost || 0),
          benefit: wa(sim.dailyBenefit, wkSim?.dailyBenefit || 0),
          avgBuy: sim.avgBuyPrice,
          avgSell: sim.avgSellPrice,
          avgPricePvHours: sim.avgPricePvHours,
          pvSavings: wa(sim.pvSavings, wkSim?.pvSavings || 0),
          pvExportRev: wa(sim.pvExportRevenue, wkSim?.pvExportRevenue || 0),
          effectivePvValue: sim.effectivePvValue,
          cycles: wa(sim.cycles, wkSim?.cycles || 0),
          arbitrageProfit: wa(sim.arbitrageProfit, wkSim?.arbitrageProfit || 0),
          pvSelfConsPct: yearlyPV > 0
            ? Math.round((wa(sim.pvSelfConsumption, wkSim?.pvSelfConsumption || 0) / yearlyPV) * 100)
            : 0,
        };
        yr.payback = invest > 0 && yr.benefit > 0 ? invest / yr.benefit : null;
        yr.profit25 = invest > 0 ? yr.benefit * 25 - invest : yr.benefit * 25;
        return { ...sc, yr };
      });

      // CROSS-TARIFF ROI: Use G11 baseline as universal reference
      // because most Polish households are on G11
      const g11Baseline = results[0].yr.baselineCost; // what you pay on G11 WITHOUT PV
      results.forEach(r => {
        const systemCost = r.yr.importCost - r.yr.exportRev; // actual cost WITH PV
        r.yr.realSaving = g11Baseline - systemCost; // real yearly saving vs G11 without PV
        r.yr.realPayback = invest > 0 && r.yr.realSaving > 0 ? invest / r.yr.realSaving : null;
        r.yr.realProfit25 = invest > 0 ? r.yr.realSaving * 25 - invest : r.yr.realSaving * 25;
      });

      // Also compute passive simulation for dynamic to get automation_gain
      const passiveScenario = { ...tariffScenarios[2], strategy: 'passive', gridChargeAllowed: false, annualDays: 365 };
      const passiveSim = this._simulateBatteryDay(profile, passiveScenario, batParams);
      const passiveBenefit = passiveSim.dailyBenefit * 365;
      const automationGain = results[2].yr.benefit - passiveBenefit;

      ct.innerHTML = results.map((r, i) => {
        const yr = r.yr;
        const realPaybackStr = yr.realPayback ? `~${yr.realPayback.toFixed(1)} lat` : (invest > 0 ? "∞ (strata)" : "— (podaj koszt)");
        const realPaybackColor = yr.realPayback ? (yr.realPayback <= 8 ? "#2ecc71" : yr.realPayback <= 15 ? "#f7b731" : "#e74c3c") : "#e74c3c";
        const realPaybackPct = yr.realPayback ? Math.min(100, (25 / yr.realPayback) * 4) : 0;
        const diffVsG11 = i > 0 ? yr.benefit - results[0].yr.benefit : 0;
        const hasArbitrage = yr.arbitrageProfit > 50 && r.strategy === 'dynamic_active';
        const hasGridCharge = yr.gridToCharge > 10;

        // Energy balance check
        const balanceIn = yr.pvTotal + yr.import;
        const balanceOut = yr.load + yr.export + yr.batteryLosses;
        const balanceOk = Math.abs(balanceIn - balanceOut) < balanceIn * 0.05;

        // Compute today's PV-hour price if live ENTSO-E data available
        let todayPvNote = '';
        if (r.liveBuyPrice && profile) {
          let todayPvWeightedSum = 0, todayPvWeightedTotal = 0;
          for (let h = 0; h < 24; h++) {
            if (profile.pv[h] > 0) {
              todayPvWeightedSum += profile.pv[h] * r.liveBuyPrice[h];
              todayPvWeightedTotal += profile.pv[h];
            }
          }
          const todayPvAvg = todayPvWeightedTotal > 0
            ? todayPvWeightedSum / todayPvWeightedTotal : 0;
          todayPvNote = `<div style="grid-column:1/-1; font-size:8px; color:#64748b; margin-top:2px; padding-top:3px; border-top:1px dashed rgba(255,255,255,0.06)">
            ⚡ Dzisiaj ENTSO-E: PV godz. = ${todayPvAvg.toFixed(2)} zł/kWh
            ${todayPvAvg < yr.avgPricePvHours * 0.7 ? ' <span style="color:#f7b731">⚠️ duck curve</span>' : ''}
          </div>`;
        }

        return `<div style="background:${r.bg}; border:1px solid ${r.border}; border-radius:14px; padding:16px; position:relative; overflow:hidden">
          ${r.badge ? `<div style="position:absolute; top:8px; right:8px; font-size:8px; color:${r.color}; font-weight:700; letter-spacing:0.5px">${r.badge}</div>` : ""}
          <div style="font-size:13px; font-weight:700; color:${r.color}; margin-bottom:4px">${r.label}</div>
          <div style="font-size:9px; color:#64748b; margin-bottom:${profileKey !== 'real' ? '4' : '12'}px; line-height:1.4">${r.desc}</div>
          ${profileKey !== 'real' ? `<div style="font-size:9px; color:#a855f7; margin-bottom:10px">⚡ ${Math.round(yearlyPV/1000)} MWh PV · 🔋 ${(batParams.capKwh || 10.2).toFixed(1)} kWh bat · ⚙️ ${(batParams.maxRate || 3).toFixed(1)} kW</div>` : ""}
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:4px; font-size:10px; margin-bottom:10px">
            <div style="color:#64748b">Śr. cena zakupu:</div><div style="color:#e74c3c; font-weight:600">${yr.avgBuy.toFixed(2)} zł/kWh</div>
            <div style="color:#64748b">Śr. cena sprzedaży:</div><div style="color:#2ecc71; font-weight:600">${yr.avgSell.toFixed(2)} zł/kWh</div>
            <div style="color:#f7b731">Śr. cena w godz. PV:</div><div style="color:#f7b731; font-weight:600">${yr.avgPricePvHours.toFixed(2)} zł/kWh ${r.liveBuyPrice ? '<span style="color:#64748b; font-size:8px">(roczna)</span>' : ''}</div>
            <div style="color:#00d4ff; font-weight:600">💎 Wartość 1 kWh PV:</div><div style="color:#00d4ff; font-weight:700">${yr.effectivePvValue.toFixed(2)} zł</div>
            ${todayPvNote}
          </div>
          <div style="background:rgba(255,255,255,0.03); border-radius:8px; padding:8px; margin-bottom:10px">
            <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:6px">⚡ Bilans energii (roczny)</div>
            <div style="display:grid; grid-template-columns:1fr auto; gap:2px 8px; font-size:10px">
              <div style="color:#f7b731">☀️ Produkcja PV:</div><div style="color:#f7b731; font-weight:600; text-align:right">${Math.round(yr.pvTotal)} kWh</div>
              <div style="color:#94a3b8; padding-left:8px">↳ autokonsumpcja:</div><div style="color:#2ecc71; font-weight:600; text-align:right">${Math.round(yr.pvSelfCons)} kWh (${yr.pvSelfConsPct}%)</div>
              ${yr.pvToGrid > 5 ? `<div style="color:#94a3b8; padding-left:8px">↳ eksport PV→sieć:</div><div style="color:#94a3b8; text-align:right">${Math.round(yr.pvToGrid)} kWh</div>` : ''}
              <div style="color:#e74c3c; margin-top:4px">🔌 Import z sieci:</div><div style="color:#e74c3c; font-weight:600; text-align:right; margin-top:4px">${Math.round(yr.import)} kWh</div>
              <div style="color:#94a3b8; padding-left:8px">↳ do domu:</div><div style="color:#94a3b8; text-align:right">${Math.round(yr.gridToLoad)} kWh</div>
              ${hasGridCharge ? `<div style="color:#a855f7; padding-left:8px">↳ do baterii (arb.):</div><div style="color:#a855f7; text-align:right">${Math.round(yr.gridToCharge)} kWh</div>` : ''}
              <div style="color:#2ecc71; margin-top:4px">↑ Eksport do sieci:</div><div style="color:#2ecc71; font-weight:600; text-align:right; margin-top:4px">${Math.round(yr.export)} kWh</div>
              ${yr.batToGrid > 10 ? `<div style="color:#a855f7; padding-left:8px">↳ bat→sieć (arb.):</div><div style="color:#a855f7; text-align:right">${Math.round(yr.batToGrid)} kWh</div>` : ''}
              <div style="color:#64748b; margin-top:4px; border-top:1px solid rgba(255,255,255,0.06); padding-top:4px">🏠 Zużycie domu:</div><div style="color:#fff; font-weight:700; text-align:right; margin-top:4px; border-top:1px solid rgba(255,255,255,0.06); padding-top:4px">${Math.round(yr.load)} kWh</div>
              ${yr.batteryLosses > 5 ? `<div style="color:#64748b">⚡ Straty baterii:</div><div style="color:#64748b; text-align:right">${Math.round(yr.batteryLosses)} kWh</div>` : ''}
              <div style="color:#94a3b8">🔋 Cykle baterii:</div><div style="color:#a855f7; font-weight:600; text-align:right">${Math.round(yr.cycles)}/rok</div>
            </div>
            <div style="font-size:8px; color:${balanceOk ? '#2ecc71' : '#e74c3c'}; margin-top:4px; text-align:right">bilans: ${balanceOk ? '✅' : '⚠️'} wejście ${Math.round(balanceIn)} = wyjście ${Math.round(balanceOut)} kWh</div>
          </div>
          <div style="margin-top:10px; padding:10px; border-radius:10px; background:rgba(0,212,255,0.06); border:1px solid rgba(0,212,255,0.15)">
            <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">💰 Roczny koszt energii z systemem</div>
            <div style="font-size:26px; font-weight:900; color:#00d4ff">${Math.round(yr.importCost - yr.exportRev).toLocaleString('pl-PL')} zł</div>
            <div style="font-size:9px; color:#94a3b8; margin-top:1px">import ${yr.importCost.toFixed(0)} zł − eksport ${yr.exportRev.toFixed(0)} zł</div>
          </div>
          <div style="border-top:1px solid rgba(255,255,255,0.06); padding-top:8px; margin-top:8px">
            <div style="font-size:9px; color:#64748b">Koszt energii bez PV (baseline G11)</div>
            <div style="font-size:13px; font-weight:600; color:#94a3b8">${Math.round(g11Baseline).toLocaleString('pl-PL')} zł/rok</div>
          </div>
          <div style="margin-top:10px; padding:10px; border-radius:10px; background:rgba(46,204,113,0.06); border:1px solid rgba(46,204,113,0.15)">
            <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">🚀 Realna roczna oszczędność</div>
            <div style="font-size:9px; color:#94a3b8; margin-bottom:2px">G11 bez PV (${Math.round(g11Baseline).toLocaleString('pl-PL')} zł) → ta taryfa z PV (${Math.round(yr.importCost - yr.exportRev).toLocaleString('pl-PL')} zł)</div>
            <div style="font-size:24px; font-weight:900; color:${yr.realSaving >= 0 ? "#2ecc71" : "#e74c3c"}">${yr.realSaving >= 0 ? "+" : ""}${Math.round(yr.realSaving).toLocaleString('pl-PL')} zł/rok</div>
            <div style="display:grid; grid-template-columns:1fr auto; gap:1px 6px; font-size:9px; margin-top:6px; padding-top:6px; border-top:1px solid rgba(255,255,255,0.06)">
              <div style="color:#f7b731">☀️ Oszcz. PV (autokon.):</div><div style="color:#f7b731; text-align:right">+${Math.round(yr.pvSavings)} zł <span style="color:#64748b">(${yr.avgPricePvHours.toFixed(2)}/kWh)</span></div>
              ${i > 0 ? `<div style="color:#00d4ff">💰 Tańsza taryfa vs G11:</div><div style="color:#00d4ff; text-align:right">+${Math.round(yr.realSaving - yr.benefit)} zł</div>` : ''}
              ${yr.pvExportRev > 5 ? `<div style="color:#2ecc71">↑ Eksport PV→sieć:</div><div style="color:#2ecc71; text-align:right">+${yr.pvExportRev.toFixed(0)} zł</div>` : ''}
              ${hasArbitrage ? `<div style="color:#a855f7">🔋 Arbitraż baterii:</div><div style="color:#a855f7; text-align:right">+${yr.arbitrageProfit.toFixed(0)} zł</div>` : ''}
              ${yr.exportRev - (yr.pvExportRev || 0) > 10 ? `<div style="color:#10b981">↑ Eksport bat→sieć:</div><div style="color:#10b981; text-align:right">+${(yr.exportRev - (yr.pvExportRev || 0)).toFixed(0)} zł</div>` : ''}
            </div>
          </div>
          ${invest > 0 ? `
          <div style="margin-top:10px">
            <div style="display:flex; justify-content:space-between; font-size:9px; color:#64748b; margin-bottom:4px">
              <span>Zwrot inwestycji (vs G11 bez PV)</span>
              <span style="color:${realPaybackColor}; font-weight:700">${realPaybackStr}</span>
            </div>
            <div style="background:rgba(255,255,255,0.08); border-radius:6px; height:8px; overflow:hidden">
              <div style="height:100%; width:${realPaybackPct}%; background:linear-gradient(90deg, ${realPaybackColor}, ${r.color}); border-radius:6px; transition:width 0.5s"></div>
            </div>
            <div style="display:flex; justify-content:space-between; font-size:9px; color:#64748b; margin-top:6px">
              <span>Zysk w 25 lat</span>
              <span style="color:${yr.realProfit25 >= 0 ? "#2ecc71" : "#e74c3c"}; font-weight:700">${yr.realProfit25 >= 0 ? "+" : ""}${Math.round(yr.realProfit25).toLocaleString("pl-PL")} zł</span>
            </div>
          </div>` : ""}
        </div>`;
      }).join("");

      // ── 3 RANKINGS + HEMS summary ──
      const rankedByRealSaving = [...results].sort((a, b) => b.yr.realSaving - a.yr.realSaving);
      const rankedByCost = [...results].sort((a, b) => (a.yr.importCost - a.yr.exportRev) - (b.yr.importCost - b.yr.exportRev));
      const rankedByBenefit = [...results].sort((a, b) => b.yr.benefit - a.yr.benefit);
      const rankColors = ['#2ecc71', '#f7b731', '#e74c3c'];
      const rankEmoji = ['🥇', '🥈', '🥉'];

      // Ranking 1 (PRIMARY): Realna roczna oszczędność (vs G11 bez PV)
      let summaryHtml = `<div style="grid-column:1/-1; margin-top:12px; padding:12px; background:rgba(46,204,113,0.06); border:1px solid rgba(46,204,113,0.15); border-radius:10px">
        <div style="font-size:10px; color:#2ecc71; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px; text-align:center">🚀 Ranking — Realna roczna oszczędność (vs G11 bez PV)</div>
        ${rankedByRealSaving.map((t, idx) => `<div style="display:flex; align-items:center; gap:8px; padding:4px 0; ${idx === 0 ? 'font-weight:700' : ''}">
          <span style="font-size:16px">${rankEmoji[idx]}</span>
          <span style="flex:1; font-size:12px; color:${t.color}">${t.label}</span>
          <span style="font-size:10px; color:#64748b; margin-right:4px">${t.yr.realPayback ? '~' + t.yr.realPayback.toFixed(1) + ' lat' : '—'}</span>
          <span style="font-size:13px; font-weight:700; color:${rankColors[idx]}">+${Math.round(t.yr.realSaving).toLocaleString('pl-PL')} zł/rok</span>
        </div>`).join('')}
      </div>`;

      // Ranking 2: Najniższy roczny koszt
      summaryHtml += `<div style="grid-column:1/-1; margin-top:8px; padding:12px; background:rgba(0,212,255,0.04); border:1px solid rgba(0,212,255,0.12); border-radius:10px">
        <div style="font-size:10px; color:#00d4ff; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px; text-align:center">💰 Ranking — Najniższy roczny koszt energii</div>
        ${rankedByCost.map((t, idx) => {
          const cost = t.yr.importCost - t.yr.exportRev;
          return `<div style="display:flex; align-items:center; gap:8px; padding:4px 0; ${idx === 0 ? 'font-weight:700' : ''}">
            <span style="font-size:16px">${rankEmoji[idx]}</span>
            <span style="flex:1; font-size:12px; color:${t.color}">${t.label}</span>
            <span style="font-size:13px; font-weight:700; color:${rankColors[idx]}">${Math.round(cost).toLocaleString('pl-PL')} zł/rok</span>
          </div>`;
        }).join('')}
      </div>`;

      // Ranking 3: Tabela podsumowująca
      const bestReal = rankedByRealSaving[0];
      const bestCost = rankedByCost[0];
      const bestBenefit = rankedByBenefit[0];
      const bestPvValue = [...results].sort((a, b) => b.yr.effectivePvValue - a.yr.effectivePvValue)[0];
      const bestHems = results[2]; // dynamic always has HEMS
      summaryHtml += `<div style="grid-column:1/-1; margin-top:8px; padding:12px; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); border-radius:10px">
        <div style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px; text-align:center">📊 Podsumowanie — Która taryfa wygrywa w czym?</div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:4px; font-size:10px">
          <div style="color:#2ecc71; font-weight:600">🚀 Realna oszczędność (ROI):</div>
          <div style="color:${bestReal.color}; font-weight:700; text-align:right">${bestReal.label.split(' — ')[0]} (+${Math.round(bestReal.yr.realSaving).toLocaleString('pl-PL')} zł, ~${bestReal.yr.realPayback?.toFixed(1) || '∞'} lat)</div>
          <div style="color:#94a3b8">💰 Najniższy roczny koszt:</div>
          <div style="color:${bestCost.color}; font-weight:700; text-align:right">${bestCost.label.split(' — ')[0]} (${Math.round(bestCost.yr.importCost - bestCost.yr.exportRev).toLocaleString('pl-PL')} zł)</div>
          <div style="color:#94a3b8">💎 Najwyższa wartość PV/kWh:</div>
          <div style="color:${bestPvValue.color}; font-weight:700; text-align:right">${bestPvValue.label.split(' — ')[0]} (${bestPvValue.yr.effectivePvValue.toFixed(2)} zł)</div>
          <div style="color:#94a3b8">🤖 Automatyka HEMS:</div>
          <div style="color:${bestHems.color}; font-weight:700; text-align:right">${bestHems.label.split(' — ')[0]} (+${automationGain.toFixed(0)} zł/rok)</div>
        </div>
      </div>`;

      // HEMS value card
      if (automationGain > 50) {
        summaryHtml += `<div style="grid-column:1/-1; margin-top:8px; padding:10px; background:rgba(168,85,247,0.08); border:1px solid rgba(168,85,247,0.2); border-radius:10px; text-align:center">
          <div style="font-size:11px; color:#a855f7">🤖 Wartość automatyki HEMS (dynamiczna)</div>
          <div style="font-size:22px; font-weight:900; color:#a855f7; margin-top:4px">+${automationGain.toFixed(0)} zł/rok</div>
          <div style="font-size:9px; color:#64748b; margin-top:2px">dodatkowa korzyść z aktywnego arbitrażu vs pasywna autokonsumpcja</div>
        </div>`;
      }
      ct.innerHTML += summaryHtml;

      // ── AI ROI Interpreter ──
      // Collect all ROI data for AI analysis
      const roiDataForAI = results.map(r => ({
        tariff: r.label,
        key: r.key,
        avgBuy: r.yr.avgBuy,
        avgSell: r.yr.avgSell,
        avgPricePvHours: r.yr.avgPricePvHours,
        effectivePvValue: r.yr.effectivePvValue,
        pvTotal: r.yr.pvTotal,
        import: r.yr.import,
        export: r.yr.export,
        load: r.yr.load,
        selfConsumption: r.yr.pvSelfCons,
        pvSavings: r.yr.pvSavings,
        pvExportRev: r.yr.pvExportRev || 0,
        importCost: r.yr.importCost,
        exportRev: r.yr.exportRev,
        annualEnergyCost: r.yr.importCost - r.yr.exportRev,
        baselineCost: r.yr.baselineCost,
        benefit: r.yr.benefit,
        realSaving: r.yr.realSaving,
        realPayback: r.yr.realPayback,
        realProfit25: r.yr.realProfit25,
        arbitrageProfit: r.yr.arbitrageProfit || 0,
        gridToCharge: r.yr.gridToCharge || 0,
        cycles: r.yr.cycles || 0,
      }));
      // Add system context for AI
      roiDataForAI.push({
        _systemContext: true,
        investmentCost: invest,
        g11BaselineCost: g11Baseline,
        yearlyConsumptionKWh: yearlyLoad || 0,
        yearlyPvProductionKWh: yearlyPV || 0,
        profileType: profileKey,
        batteryCapacityKWh: (this._settings.roi_battery || 10000) / 1000,
        pvPeakKWp: (this._settings.roi_pv_peak || 0) / 1000,
        automationGainHEMS: automationGain || 0,
      });
      this._roiDataForAI = roiDataForAI;

      const aiInterpretHtml = `
        <div style="grid-column:1/-1; margin-top:12px; padding:14px; background:linear-gradient(135deg, rgba(0,212,255,0.06) 0%, rgba(168,85,247,0.06) 100%); border:1px solid rgba(0,212,255,0.15); border-radius:12px">
          <div style="display:flex; align-items:center; gap:8px; margin-bottom:10px">
            <span style="font-size:20px">🧠</span>
            <span style="font-size:12px; font-weight:700; color:#00d4ff">AI Interpreter — Wyjaśnienie wyników ROI</span>
          </div>
          <div style="font-size:9px; color:#64748b; margin-bottom:10px; line-height:1.5">
            AI przeanalizuje wyniki symulacji i wyjaśni je prostym językiem — co oznaczają te liczby dla Twojego portfela i którą taryfę wybrać.
          </div>
          <button id="btn-roi-ai-analyze"
            onclick="this.getRootNode().host._triggerRoiAiAnalysis()"
            style="width:100%; padding:10px 16px; background:linear-gradient(135deg, #00d4ff 0%, #a855f7 100%); color:#fff; border:none; border-radius:8px; font-size:12px; font-weight:700; cursor:pointer; letter-spacing:0.5px; transition:all 0.2s">
            🧠 Analizuj z AI
          </button>
          <div id="roi-ai-status" style="font-size:9px; color:#64748b; text-align:center; margin-top:6px"></div>
          <div id="roi-ai-result" style="display:none; margin-top:12px; padding:12px; background:rgba(0,0,0,0.2); border-radius:8px; border:1px solid rgba(255,255,255,0.06); font-size:11px; color:#e2e8f0; line-height:1.6; overflow-wrap:break-word; word-break:break-word; min-width:0"></div>
        </div>
      `;
      ct.innerHTML += aiInterpretHtml;

      // Restore AI state after re-render (button/status/result are destroyed each cycle)
      if (this._roiAiLoading) {
        // Analysis in progress — restore loading state
        const btn = this.shadowRoot.getElementById('btn-roi-ai-analyze');
        const status = this.shadowRoot.getElementById('roi-ai-status');
        if (btn) { btn.disabled = true; btn.style.opacity = '0.5'; btn.innerHTML = '<span style="animation:pulse 1s infinite">⏳</span> Analizuję...'; }
        if (status) { status.textContent = '🧠 AI analizuje dane ROI — to może potrwać do 30 sekund...'; status.style.color = '#00d4ff'; }
      } else {
        // Not loading — show cached result if available
        const cachedAnalysis = this._roiAiCachedResult || this._settings?.ai_roi_analysis;
        if (cachedAnalysis?.text) {
          this._displayRoiAiResult(cachedAnalysis);
        }
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

  _saveEnergyProvider() {
    const sel = this.shadowRoot.getElementById("sel-energy-provider");
    if (!sel) return;
    this._settings.energy_provider = sel.value;
    this._savePanelSettings({ energy_provider: sel.value });
    // Rebuild tariff options for new provider
    this._rebuildTariffOptions();
    // Update pricing table
    this._updatePricingTable();
    this._updateG13Timeline();
  }

  _rebuildTariffOptions() {
    const provider = this._settings.energy_provider || 'tauron';
    const tariffs = SH_PROVIDER_TARIFFS[provider] || SH_PROVIDER_TARIFFS.tauron;
    const sel = this.shadowRoot.getElementById('sel-tariff-plan');
    if (!sel) return;
    const currentVal = sel.value;
    sel.innerHTML = '';
    tariffs.forEach(t => {
      const opt = document.createElement('option');
      opt.value = t.value;
      opt.textContent = t.label;
      sel.appendChild(opt);
    });
    // Keep current value if still valid, otherwise pick first
    const validValues = tariffs.map(t => t.value);
    if (validValues.includes(currentVal)) {
      sel.value = currentVal;
    } else {
      sel.value = validValues[0];
      this._settings.tariff_plan = sel.value;
      this._savePanelSettings({ tariff_plan: sel.value });
    }
  }

  /** Get current provider key */
  _getProvider() {
    return this._settings.energy_provider || 'tauron';
  }

  /** Shared tariff helper — returns zone info for any supported tariff */
  _getTariffInfo() {
    const tariff = this._settings.tariff_plan || "G13";
    const provider = this._getProvider();
    const prices = (SH_PROVIDER_PRICES[provider] || SH_PROVIDER_PRICES.tauron);
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
      const p = prices.G11 || { flat: 0.87 };
      zone = "flat"; zoneName = "STAŁA"; zoneColor = "#3b82f6"; price = p.flat.toFixed(2);
    } else if (tariff === "G12n" && provider === "pge") {
      // PGE G12n — niedzielna: Mon-Sat peak 05:00-01:00, off-peak 01:00-05:00; Sun all off-peak
      const p = prices.G12n || { off_peak: 0.59, peak: 1.21 };
      const isSunday = (dow === 0);
      if (isSunday) {
        zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = p.off_peak.toFixed(2);
      } else if ((h >= 5 && h < 25) && !(h >= 1 && h < 5)) {
        // peak: 05:00 → 01:00 next day (h>=5 || h<1)
        if (h >= 5 || h < 1) {
          zone = "peak"; zoneName = "SZCZYT"; zoneColor = "#e74c3c"; price = p.peak.toFixed(2);
        } else {
          zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = p.off_peak.toFixed(2);
        }
      } else {
        zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = p.off_peak.toFixed(2);
      }
    } else if (tariff === "G12") {
      const p = prices.G12 || { off_peak: 0.55, peak: 1.10 };
      if (provider === "pge") {
        // PGE G12 — different summer/winter schedules
        if (isSummer) {
          // Summer: off-peak 15:00–17:00 + 22:00–06:00
          if ((h >= 15 && h < 17) || h >= 22 || h < 6) {
            zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = p.off_peak.toFixed(2);
          } else {
            zone = "peak"; zoneName = "SZCZYT"; zoneColor = "#e74c3c"; price = p.peak.toFixed(2);
          }
        } else {
          // Winter: off-peak 13:00–15:00 + 22:00–06:00
          if ((h >= 13 && h < 15) || h >= 22 || h < 6) {
            zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = p.off_peak.toFixed(2);
          } else {
            zone = "peak"; zoneName = "SZCZYT"; zoneColor = "#e74c3c"; price = p.peak.toFixed(2);
          }
        }
      } else {
        // Tauron G12
        if ((h >= 13 && h < 15) || h >= 22 || h < 6) {
          zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = p.off_peak.toFixed(2);
        } else {
          zone = "peak"; zoneName = "SZCZYT"; zoneColor = "#e74c3c"; price = p.peak.toFixed(2);
        }
      }
    } else if (tariff === "G12w") {
      const p = prices.G12w || { off_peak: 0.55, peak: 1.10 };
      if (provider === "pge") {
        // PGE G12w — weekends all off-peak; workdays same as G12
        if (isWeekend) {
          zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = p.off_peak.toFixed(2);
        } else if (isSummer) {
          if ((h >= 15 && h < 17) || h >= 22 || h < 6) {
            zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = p.off_peak.toFixed(2);
          } else {
            zone = "peak"; zoneName = "SZCZYT"; zoneColor = "#e74c3c"; price = p.peak.toFixed(2);
          }
        } else {
          if ((h >= 13 && h < 15) || h >= 22 || h < 6) {
            zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = p.off_peak.toFixed(2);
          } else {
            zone = "peak"; zoneName = "SZCZYT"; zoneColor = "#e74c3c"; price = p.peak.toFixed(2);
          }
        }
      } else {
        // Tauron G12w
        if (isWeekend || (h >= 13 && h < 15) || h >= 22 || h < 6) {
          zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = p.off_peak.toFixed(2);
        } else {
          zone = "peak"; zoneName = "SZCZYT"; zoneColor = "#e74c3c"; price = p.peak.toFixed(2);
        }
      }
    } else {
      // G13 (Tauron only)
      const p = prices.G13 || { off_peak: 0.63, morning: 0.91, peak: 1.50 };
      if (isWeekend) {
        zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = p.off_peak.toFixed(2);
      } else if (h >= 7 && h < 13) {
        zone = "morning"; zoneName = "PORANNY"; zoneColor = "#e67e22"; price = p.morning.toFixed(2);
      } else if (isSummer) {
        if (h >= 19 && h < 22) { zone = "peak"; zoneName = "SZCZYT"; zoneColor = "#e74c3c"; price = p.peak.toFixed(2); }
        else { zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = p.off_peak.toFixed(2); }
      } else {
        if (h >= 16 && h < 21) { zone = "peak"; zoneName = "SZCZYT"; zoneColor = "#e74c3c"; price = p.peak.toFixed(2); }
        else { zone = "off"; zoneName = "OFF-PEAK"; zoneColor = "#2ecc71"; price = p.off_peak.toFixed(2); }
      }
    }

    return { tariff, provider, zone, zoneName, zoneColor, price, isWeekend, isSummer, hour: h };
  }

  /** Average tariff price for savings calculations */
  _getTariffAvgPrice() {
    const t = this._settings.tariff_plan || "G13";
    const provider = this._getProvider();
    if (t === "Dynamic") return parseFloat(this._haState("sensor.entso_e_srednia_dzisiaj")) || 0.50;
    const prices = (SH_PROVIDER_PRICES[provider] || SH_PROVIDER_PRICES.tauron);
    if (t === "G11") return (prices.G11 || {}).flat || 0.87;
    if (t === "G12") {
      const p = prices.G12 || { off_peak: 0.55, peak: 1.10 };
      return p.off_peak * 0.7 + p.peak * 0.3; // rough weighted avg
    }
    if (t === "G12w") {
      const p = prices.G12w || { off_peak: 0.55, peak: 1.10 };
      return p.off_peak * 0.75 + p.peak * 0.25;
    }
    if (t === "G12n" && provider === "pge") {
      const p = prices.G12n || { off_peak: 0.59, peak: 1.21 };
      return p.off_peak * 0.3 + p.peak * 0.7; // G12n has only 4h off-peak
    }
    // G13 (Tauron)
    const gp = prices.G13 || { off_peak: 0.63, morning: 0.91, peak: 1.50 };
    return gp.off_peak * 0.85 + gp.morning * 0.05 + gp.peak * 0.10;
  }

  _updatePricingTable() {
    const provider = this._getProvider();
    const prices = SH_PROVIDER_PRICES[provider] || SH_PROVIDER_PRICES.tauron;
    const label = prices.label || 'Tauron';
    const titleEl = this.shadowRoot.getElementById('pricing-table-title');
    const bodyEl = this.shadowRoot.getElementById('pricing-table-body');
    const footerEl = this.shadowRoot.getElementById('pricing-table-footer');
    if (!bodyEl) return;
    if (titleEl) titleEl.textContent = `📋 Cennik ${label} 2026 — porównanie taryf`;

    // Define rows per provider
    const rows = [];
    const tariff = this._settings.tariff_plan || 'G13';
    const selStyle = (t) => t === tariff ? 'background:rgba(46,204,113,0.06)' : 'border-bottom:1px solid rgba(255,255,255,0.05)';
    const selTd = (t) => t === tariff ? 'font-weight:800; color:#2ecc71' : '';
    const selMark = (t) => t === tariff ? ' ✓' : '';

    if (provider === 'pge') {
      rows.push({ name: 'G11', prices_2k: '1.33', prices_4k: '1.24', prices_6k: '1.20', hours: 'brak — stała', key: 'G11' });
      rows.push({ name: 'G12', prices_2k: '1.01', prices_4k: '0.90', prices_6k: '0.85', hours: 'lato: 15–17+22–06 / zima: 13–15+22–06', key: 'G12' });
      rows.push({ name: 'G12w', prices_2k: '1.08', prices_4k: '0.97', prices_6k: '0.92', hours: 'jak G12 + weekendy cała doba', key: 'G12w' });
      rows.push({ name: 'G12n', prices_2k: '0.97', prices_4k: '0.86', prices_6k: '0.81', hours: 'noc 1–5 + niedziele cała doba', key: 'G12n' });
    } else {
      // Tauron
      rows.push({ name: 'G11', prices_2k: '1.22', prices_4k: '1.12', prices_6k: '1.07', hours: 'brak — stała', key: 'G11' });
      rows.push({ name: 'G12', prices_2k: '0.97', prices_4k: '0.87', prices_6k: '0.82', hours: '13–15 + 22–06', key: 'G12' });
      rows.push({ name: 'G12w', prices_2k: '0.99', prices_4k: '0.90', prices_6k: '0.85', hours: '13–15 + 22–06 + weekendy', key: 'G12w' });
      rows.push({ name: 'G13', prices_2k: '0.97', prices_4k: '0.87', prices_6k: '0.83', hours: '85% off-peak + weekendy', key: 'G13' });
    }

    let html = `<table style="width:100%; border-collapse:collapse; font-size:10px; color:#cbd5e1">
      <thead><tr style="border-bottom:1px solid rgba(255,255,255,0.1)">
        <th style="text-align:left; padding:6px; color:#64748b; font-weight:700">Taryfa</th>
        <th style="text-align:center; padding:6px; color:#64748b">2000 kWh</th>
        <th style="text-align:center; padding:6px; color:#64748b">4000 kWh</th>
        <th style="text-align:center; padding:6px; color:#64748b">6000 kWh</th>
        <th style="text-align:center; padding:6px; color:#64748b">Godziny taniej</th>
      </tr></thead><tbody>`;

    rows.forEach(r => {
      const s = selStyle(r.key);
      const td = selTd(r.key);
      const mark = selMark(r.key);
      html += `<tr style="${s}">
        <td style="padding:6px; font-weight:700; ${td}">${r.name}${mark}</td>
        <td style="text-align:center; padding:6px; ${td}">${r.prices_2k} zł</td>
        <td style="text-align:center; padding:6px; ${td}">${r.prices_4k} zł</td>
        <td style="text-align:center; padding:6px; ${td}">${r.prices_6k} zł</td>
        <td style="text-align:center; padding:6px; color:${r.key === tariff ? '#2ecc71' : (r.hours.startsWith('brak') ? '#94a3b8' : '#2ecc71')}; ${r.key === tariff ? 'font-weight:700' : ''}">${r.hours}</td>
      </tr>`;
    });

    // Dynamic row
    html += `<tr style="border-top:2px solid rgba(168,85,247,0.3); background:rgba(168,85,247,0.06)">
      <td style="padding:6px; font-weight:800; color:#a855f7">⚡ Dynamiczna${'Dynamic' === tariff ? ' ✓' : ''}</td>
      <td style="text-align:center; padding:6px; color:#a855f7">zmienna</td>
      <td style="text-align:center; padding:6px; color:#a855f7">zmienna</td>
      <td style="text-align:center; padding:6px; color:#a855f7">zmienna</td>
      <td style="text-align:center; padding:6px; color:#a855f7; font-weight:700">cena co godzinę wg ENTSO-E</td>
    </tr>`;
    html += '</tbody></table>';
    bodyEl.innerHTML = html;

    if (footerEl) {
      if (provider === 'pge') {
        footerEl.innerHTML = '* Ceny brutto za kWh (sprzedaż + dystrybucja) dla zużycia 80/20 (strefa tańsza/droższa).<br>Stawki 2026 r. zatwierdzone przez URE. Instalacja 3-fazowa.';
      } else {
        footerEl.innerHTML = '* Ceny brutto za kWh (sprzedaż + dystrybucja). G13: 5% przedpołudniowa, 10% popołudniowa, 85% off-peak.<br>Weekendy i święta = cały dzień off-peak (najtańsza strefa).';
      }
    }
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
    this._windDataLoaded = true;
  }

  _loadWindData() {
    this._windLoading = true;
    const wt = this._settings.wind_turbine;
    if (!wt) {
      this._applyWindPreset('medium', true);
      this._windDataLoaded = true;
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
    this._windDataLoaded = true;
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
    // Safety: never save all-zero config (protects against save when inputs are empty/hidden)
    if (wt.power_kw === 0 && wt.rotor_diameter === 0 && wt.investment === 0) {
      console.warn('[SH] Wind save blocked — all values are zero (inputs not loaded yet)');
      return;
    }
    this._savePanelSettings({ wind_turbine: wt, wind_turbine_preset: this._windActivePreset });
    const st = this.shadowRoot.getElementById('wind-save-status');
    if (st) { st.textContent = '✅ Zapisano konfigurację turbiny!'; setTimeout(() => { st.textContent = ''; }, 4000); }
    // Recalculate wind calendar with new turbine params
    if (this._hass) {
      this._hass.callService('smartinghome', 'recalculate_wind_calendar', {})
        .then(() => { if (this._wcListenersAttached) this._fetchWindCalData(); })
        .catch(e => console.warn('[SH] Wind recalculate error:', e));
    }
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
    // Don't run calculations until wind data has been loaded into the inputs
    // Otherwise, input fields are empty (0) and auto-save would overwrite real data
    if (!this._windDataLoaded) return;

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
    // Update wind calendar today card
    this._updateWindCalendarToday();
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
    // Auto-save wind data with debounce (1.5s) — skip during initial load AND before data is loaded
    if (!this._windLoading && this._windDataLoaded) {
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

  /* ═══════════════════════════════════════════════
     ✦  WIND CALENDAR — Frontend Engine
     ═══════════════════════════════════════════════ */
  _wcPeriod = 'week';
  _wcOffset = 0;
  _wcData = null;
  _wcListenersAttached = false;

  _initWindCalendar() {
    if (this._wcListenersAttached) return;
    this._wcListenersAttached = true;
    // Listen for backend calendar data events
    if (this._hass) {
      this._hass.connection.subscribeEvents((ev) => {
        this._wcData = ev.data;
        this._renderWindCalendarUI();
      }, 'smartinghome_wind_calendar_data');
    }
    // Initial load
    this._windCalNavigate(0);
  }

  _switchWindCalPeriod(period) {
    this._wcPeriod = period;
    this._wcOffset = 0;
    const root = this.shadowRoot;
    ['week','month','year','all'].forEach(p => {
      const btn = root.getElementById(`wc-period-${p}`);
      if (btn) btn.classList.toggle('active', p === period);
    });
    this._fetchWindCalData();
  }

  _windCalNavigate(dir) {
    if (dir === 0) {
      this._wcOffset = 0;
    } else {
      this._wcOffset += dir;
    }
    this._fetchWindCalData();
  }

  _fetchWindCalData() {
    if (!this._hass) return;
    const {start, end, label} = this._getWindCalRange();
    const labelEl = this.shadowRoot.getElementById('wc-period-label');
    if (labelEl) labelEl.textContent = label;

    this._hass.callService('smartinghome', 'get_wind_calendar', {
      start_date: start,
      end_date: end,
    }).catch(e => console.warn('[SH] Wind calendar fetch error:', e));
  }

  _getWindCalRange() {
    const now = new Date();
    let start, end, label;
    const fmt = (d) => d.toISOString().split('T')[0];
    const plMonth = ['Styczeń','Luty','Marzec','Kwiecień','Maj','Czerwiec','Lipiec','Sierpień','Wrzesień','Październik','Listopad','Grudzień'];

    switch (this._wcPeriod) {
      case 'week': {
        const ref = new Date(now);
        ref.setDate(ref.getDate() + this._wcOffset * 7);
        const day = ref.getDay() || 7;
        const monday = new Date(ref);
        monday.setDate(ref.getDate() - day + 1);
        const sunday = new Date(monday);
        sunday.setDate(monday.getDate() + 6);
        start = fmt(monday);
        end = fmt(sunday);
        label = `${monday.getDate()}.${monday.getMonth()+1} – ${sunday.getDate()}.${sunday.getMonth()+1}.${sunday.getFullYear()}`;
        break;
      }
      case 'month': {
        const ref = new Date(now.getFullYear(), now.getMonth() + this._wcOffset, 1);
        const last = new Date(ref.getFullYear(), ref.getMonth() + 1, 0);
        start = fmt(ref);
        end = fmt(last);
        label = `${plMonth[ref.getMonth()]} ${ref.getFullYear()}`;
        break;
      }
      case 'year': {
        const y = now.getFullYear() + this._wcOffset;
        start = `${y}-01-01`;
        end = `${y}-12-31`;
        label = `Rok ${y}`;
        break;
      }
      case 'all':
      default:
        start = '';
        end = '';
        label = 'Cały okres (do 5 lat)';
        break;
    }
    return { start, end, label };
  }

  _renderWindCalendarUI() {
    if (!this._wcData || this._wcData.error) {
      console.warn('[SH] Wind calendar data error:', this._wcData?.error);
      return;
    }

    const { days, summary, meta } = this._wcData;
    if (!days || !summary) return;

    const dayEntries = Object.entries(days).sort((a,b) => a[0].localeCompare(b[0]));

    // Update KPI cards
    this._setText('wc-kpi-kwh', (summary.total_kwh || 0).toFixed(1));
    this._setText('wc-kpi-revenue', (summary.total_revenue || 0).toFixed(2));
    this._setText('wc-kpi-days', summary.productive_days || 0);
    this._setText('wc-kpi-days-total', `/ ${summary.total_days || 0} łącznie`);
    this._setText('wc-kpi-cf', `${((summary.avg_capacity_factor || 0) * 100).toFixed(1)}%`);

    // Best day
    if (summary.best_day) {
      this._setText('wc-kpi-best', `${(summary.best_day.kwh || 0).toFixed(1)} kWh`);
      this._setText('wc-kpi-best-date', summary.best_day.date || '—');
    }
    this._setText('wc-kpi-wind', ((summary.avg_wind_kmh || 0)).toFixed(1));

    // Chart subtitle
    const subtitleEl = this.shadowRoot.getElementById('wc-chart-subtitle');
    if (subtitleEl) {
      subtitleEl.textContent = `${dayEntries.length} dni · Avg wiatr: ${(summary.avg_wind_kmh || 0).toFixed(1)} km/h · Produkcja: ${(summary.total_kwh || 0).toFixed(1)} kWh`;
    }

    // Render daily bar chart
    this._renderWindCalDailyChart(dayEntries);

    // Wind distribution
    this._renderWindDistribution(dayEntries);

    // ROI
    this._renderWindCalROI(summary, dayEntries.length);

    // Recommendation
    this._renderWindCalRecommendation(summary);
  }

  _renderWindCalDailyChart(dayEntries) {
    const chartEl = this.shadowRoot.getElementById('wc-daily-chart');
    if (!chartEl || dayEntries.length === 0) {
      if (chartEl) chartEl.innerHTML = '<div style="color:#64748b; font-size:11px; text-align:center; width:100%">Brak danych dla wybranego okresu</div>';
      return;
    }

    const maxKwh = Math.max(...dayEntries.map(([,d]) => d.kwh_produced || 0), 0.1);
    const barWidth = Math.max(4, Math.min(20, Math.floor(600 / dayEntries.length)));

    chartEl.innerHTML = dayEntries.map(([date, d]) => {
      const kwh = d.kwh_produced || 0;
      const h = Math.max(2, (kwh / maxKwh) * 140);
      const profitable = kwh > 0 && (d.revenue_pln || 0) > 0;
      const color = kwh === 0 ? '#475569' : profitable ? '#2ecc71' : '#e74c3c';
      const shortDate = date.slice(5); // MM-DD
      const tooltip = `${date}: ${kwh.toFixed(2)} kWh, ${(d.avg_wind_kmh || 0).toFixed(1)} km/h`;
      return `<div style="flex:0 0 ${barWidth}px; display:flex; flex-direction:column; align-items:center; gap:1px" title="${tooltip}">
        <div style="width:${Math.max(3, barWidth - 2)}px; height:${h}px; background:${color}; border-radius:2px 2px 0 0; opacity:0.85; transition:height 0.3s"></div>
        ${dayEntries.length <= 31 ? `<div style="font-size:6px; color:#64748b; writing-mode:vertical-lr; transform:rotate(180deg); height:24px; overflow:hidden">${shortDate}</div>` : ''}
      </div>`;
    }).join('');
  }

  _renderWindDistribution(dayEntries) {
    // Classify days by avg wind (m/s)
    let calm = 0, light = 0, moderate = 0, strong = 0, vstrong = 0;
    dayEntries.forEach(([, d]) => {
      const ms = (d.avg_wind_kmh || 0) / 3.6;
      if (ms < 2) calm++;
      else if (ms < 4) light++;
      else if (ms < 6) moderate++;
      else if (ms < 8) strong++;
      else vstrong++;
    });
    this._setText('wc-dist-calm', calm);
    this._setText('wc-dist-light', light);
    this._setText('wc-dist-moderate', moderate);
    this._setText('wc-dist-strong', strong);
    this._setText('wc-dist-vstrong', vstrong);
  }

  _renderWindCalROI(summary, totalDays) {
    const el = this.shadowRoot.getElementById('wc-roi-content');
    if (!el) return;

    const g = (id) => parseFloat(this.shadowRoot.getElementById(id)?.value) || 0;
    const investment = g('wind-turbine-investment') || 25000;
    const nominalKw = g('wind-turbine-power') || 3;
    const priceKwh = g('wind-turbine-price') || 0.87;

    if (totalDays < 7) {
      el.innerHTML = '<div style="text-align:center; color:#64748b; font-size:12px; padding:16px">Potrzebujesz minimum 7 dni danych aby wyświetlić analizę opłacalności.</div>';
      return;
    }

    const kwhPerDay = (summary.total_kwh || 0) / (totalDays || 1);
    const revenuePerDay = kwhPerDay * priceKwh;
    const yearlyKwh = kwhPerDay * 365;
    const yearlySavings = yearlyKwh * priceKwh;
    const payback = yearlySavings > 0 ? investment / yearlySavings : null;
    const profit20 = yearlySavings * 20 - investment;
    const cf = nominalKw > 0 ? (kwhPerDay / (nominalKw * 24)) * 100 : 0;
    const productiveRatio = totalDays > 0 ? ((summary.productive_days || 0) / totalDays * 100) : 0;

    const paybackColor = payback && payback <= 10 ? '#2ecc71' : payback && payback <= 15 ? '#f7b731' : '#e74c3c';

    el.innerHTML = `
      <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(160px, 1fr)); gap:10px; margin-bottom:12px">
        <div style="background:rgba(0,212,255,0.06); border:1px solid rgba(0,212,255,0.12); border-radius:12px; padding:14px; text-align:center">
          <div style="font-size:9px; color:#64748b; text-transform:uppercase">📊 Ekstrapolacja roczna</div>
          <div style="font-size:24px; font-weight:900; color:#f7b731; margin-top:4px">${yearlyKwh.toFixed(0)} kWh</div>
          <div style="font-size:10px; color:#94a3b8">~${kwhPerDay.toFixed(2)} kWh/dzień</div>
        </div>
        <div style="background:rgba(46,204,113,0.06); border:1px solid rgba(46,204,113,0.12); border-radius:12px; padding:14px; text-align:center">
          <div style="font-size:9px; color:#64748b; text-transform:uppercase">💰 Oszczędność roczna</div>
          <div style="font-size:24px; font-weight:900; color:#2ecc71; margin-top:4px">${yearlySavings.toFixed(0)} zł</div>
          <div style="font-size:10px; color:#94a3b8">~${revenuePerDay.toFixed(2)} zł/dzień</div>
        </div>
        <div style="background:rgba(${paybackColor === '#2ecc71' ? '46,204,113' : paybackColor === '#f7b731' ? '247,183,49' : '231,76,60'},0.06); border:1px solid rgba(${paybackColor === '#2ecc71' ? '46,204,113' : paybackColor === '#f7b731' ? '247,183,49' : '231,76,60'},0.12); border-radius:12px; padding:14px; text-align:center">
          <div style="font-size:9px; color:#64748b; text-transform:uppercase">⏱️ Zwrot inwestycji</div>
          <div style="font-size:24px; font-weight:900; color:${paybackColor}; margin-top:4px">${payback ? `~${payback.toFixed(1)} lat` : '—'}</div>
          <div style="font-size:10px; color:#94a3b8">Inwestycja: ${investment.toLocaleString('pl-PL')} zł</div>
        </div>
        <div style="background:rgba(${profit20 >= 0 ? '46,204,113' : '231,76,60'},0.06); border:1px solid rgba(${profit20 >= 0 ? '46,204,113' : '231,76,60'},0.12); border-radius:12px; padding:14px; text-align:center">
          <div style="font-size:9px; color:#64748b; text-transform:uppercase">🏆 Zysk w 20 lat</div>
          <div style="font-size:24px; font-weight:900; color:${profit20 >= 0 ? '#2ecc71' : '#e74c3c'}; margin-top:4px">${profit20 >= 0 ? '+' : ''}${Math.round(profit20).toLocaleString('pl-PL')} zł</div>
          <div style="font-size:10px; color:#94a3b8">CF: ${cf.toFixed(1)}% · ${productiveRatio.toFixed(0)}% dni produktywnych</div>
        </div>
      </div>
      <div style="font-size:10px; color:#64748b; text-align:center; line-height:1.5">
        📋 Obliczenia na podstawie <strong>${totalDays}</strong> dni rzeczywistych pomiarów · Turbina: ${nominalKw} kW · Cena: ${priceKwh} zł/kWh
      </div>`;
  }

  _renderWindCalRecommendation(summary) {
    const el = this.shadowRoot.getElementById('wc-recommendation');
    if (!el) return;

    const totalDays = summary.total_days || 0;
    if (totalDays < 7) {
      el.innerHTML = '<div style="text-align:center; color:#64748b; font-size:12px; padding:8px">Zbierz minimum 7 dni danych aby zobaczyć rekomendację.</div>';
      return;
    }

    const avgMs = (summary.avg_wind_kmh || 0) / 3.6;
    const productivePct = totalDays > 0 ? ((summary.productive_days || 0) / totalDays) * 100 : 0;
    const kwhPerDay = (summary.total_kwh || 0) / totalDays;

    const g = (id) => parseFloat(this.shadowRoot.getElementById(id)?.value) || 0;
    const investment = g('wind-turbine-investment') || 25000;
    const priceKwh = g('wind-turbine-price') || 0.87;
    const yearlySavings = kwhPerDay * 365 * priceKwh;
    const payback = yearlySavings > 0 ? investment / yearlySavings : null;

    let icon, title, color, advice;

    if (avgMs >= 5 && productivePct >= 50 && payback && payback <= 12) {
      icon = '✅'; title = 'Lokalizacja KORZYSTNA'; color = '#2ecc71';
      advice = `Średnia prędkość wiatru <strong>${avgMs.toFixed(1)} m/s</strong> z <strong>${productivePct.toFixed(0)}%</strong> dni produkcyjnych (${summary.productive_days}/${totalDays}) zapewnia opłacalną eksploatację turbiny wiatrowej. Szacowany zwrot inwestycji: <strong>~${payback.toFixed(1)} lat</strong>.`;
    } else if (avgMs >= 3.5 && productivePct >= 30) {
      icon = '⚠️'; title = 'Lokalizacja UMIARKOWANA'; color = '#f7b731';
      advice = `Średnia prędkość wiatru <strong>${avgMs.toFixed(1)} m/s</strong> z <strong>${productivePct.toFixed(0)}%</strong> dni produkcyjnych. Opłacalność zależy od wyboru turbiny — rozważ model o niskim progu startu (cut-in &lt; 2.5 m/s) lub system hybrydowy PV + wiatr. ${payback ? `Szacowany zwrot: ~${payback.toFixed(1)} lat.` : ''}`;
    } else {
      icon = '❌'; title = 'Lokalizacja NIEKORZYSTNA'; color = '#e74c3c';
      advice = `Średnia prędkość wiatru <strong>${avgMs.toFixed(1)} m/s</strong> z zaledwie <strong>${productivePct.toFixed(0)}%</strong> dni produkcyjnych to za mało dla opłacalnej turbiny wiatrowej. ${payback ? `Zwrot dopiero za ~${payback.toFixed(0)} lat.` : ''} Zalecamy inwestycję w fotowoltaikę lub system hybrydowy z magazynem energii.`;
    }

    el.innerHTML = `
      <div style="font-size:48px; text-align:center; margin-bottom:8px">${icon}</div>
      <div style="font-size:16px; font-weight:800; color:${color}; text-align:center">${title}</div>
      <div style="font-size:11px; color:#94a3b8; text-align:center; margin-top:6px; line-height:1.6">${advice}</div>
      <div style="font-size:9px; color:#475569; text-align:center; margin-top:8px">Na podstawie ${totalDays} dni pomiarów z kalendarza wiatrowego</div>`;
  }

  _updateWindCalendarToday() {
    // Update today's live card from Ecowitt sensor data
    if (!this._hass?.states) return;
    const n = (id) => {
      const st = this._hass.states[id];
      return (st && st.state !== 'unknown' && st.state !== 'unavailable') ? parseFloat(st.state) : null;
    };
    const wind = n('sensor.ecowitt_wind_speed_9747');
    if (wind === null) return;

    const g = (id) => parseFloat(this.shadowRoot.getElementById(id)?.value) || 0;
    const cutIn = g('wind-turbine-cutin') || 3;
    const diameter = g('wind-turbine-diameter') || 3.2;
    const nominalKw = g('wind-turbine-power') || 3;
    const priceKwh = g('wind-turbine-price') || 0.87;
    const windMs = wind / 3.6;

    // Estimate instantaneous power
    let power = 0;
    if (windMs >= cutIn) {
      power = this._calcWindPower(wind, diameter);
      if (power > nominalKw * 1000) power = nominalKw * 1000;
    }
    const dailyKwh = (power * 24) / 1000; // rough estimate based on current speed
    const dailyRevenue = dailyKwh * priceKwh;

    // Use _windCalSamples to track how many updates we've seen
    if (!this._windCalSamples) this._windCalSamples = 0;
    this._windCalSamples++;

    this._setText('wc-today-samples', this._windCalSamples);
    this._setText('wc-today-avgwind', `${wind.toFixed(1)} km/h`);
    this._setText('wc-today-kwh', dailyKwh.toFixed(2));
    this._setText('wc-today-revenue', `${dailyRevenue.toFixed(2)} zł`);
    this._setText('wc-today-productive', windMs >= cutIn ? '✅ Tak' : '⏸️ Nie');
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
    const rceSell = parseFloat(this._s("sensor.rce_pse_cena_sprzedazy_prosument") || "0");
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

    // RCE data — v2.0 entity names (NC-RCE PSE migration)
    const rceMwh = parseFloat(this._s("sensor.rce_pse_cena") || "0");
    const rceKwh = rceMwh > 0 ? rceMwh / 1000 : 0;  // v2: cena_za_kwh removed, compute from MWh
    const rceNext = parseFloat(this._s("sensor.rce_pse_cena_nastepny_okres") || "0");  // v2: was cena_nastepnej_godziny
    const rceCheapWin = this._s("binary_sensor.rce_pse_tanie_okno_aktywne");  // v2: was aktywne_najtansze_okno_dzisiaj
    const rceExpWin = this._s("binary_sensor.rce_pse_drogie_okno_aktywne");  // v2: was aktywne_najdrozsze_okno_dzisiaj

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

    // ── Dynamic HEMS force button highlighting ──
    const btnCharge = this.shadowRoot.getElementById("hems-btn-charge");
    const btnDischarge = this.shadowRoot.getElementById("hems-btn-discharge");
    const btnStop = this.shadowRoot.getElementById("hems-btn-stop");
    const neutralStyle = { bg: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)', color: '#94a3b8' };
    const resetBtn = (btn) => {
      if (!btn) return;
      btn.style.background = neutralStyle.bg;
      btn.style.border = neutralStyle.border;
      btn.style.color = neutralStyle.color;
      btn.style.boxShadow = 'none';
    };
    resetBtn(btnCharge); resetBtn(btnDischarge); resetBtn(btnStop);
    if (invMode === 'eco_charge') {
      if (btnCharge) {
        btnCharge.style.background = 'rgba(46,204,113,0.15)';
        btnCharge.style.border = '2px solid #2ecc71';
        btnCharge.style.color = '#2ecc71';
        btnCharge.style.boxShadow = '0 0 12px rgba(46,204,113,0.3)';
      }
    } else if (invMode === 'eco_discharge') {
      if (btnDischarge) {
        btnDischarge.style.background = 'rgba(231,76,60,0.15)';
        btnDischarge.style.border = '2px solid #e74c3c';
        btnDischarge.style.color = '#e74c3c';
        btnDischarge.style.boxShadow = '0 0 12px rgba(231,76,60,0.3)';
      }
    }

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

  /* ═══ FORECAST CHARTS — ±12h Prognoza vs Rzeczywistość ═══ */

  async _fetchForecastChartData() {
    // Cache for 5 minutes
    if (this._fcChartCache && (Date.now() - this._fcChartCacheTs) < 300000) return this._fcChartCache;
    if (!this._hass?.connection) {
      console.warn('[SH] Forecast charts: no hass connection');
      return null;
    }
    console.log('[SH] Forecast charts: fetching fresh data...');

    const now = new Date();
    const nowHour = now.getHours();
    const start12h = new Date(now); start12h.setHours(start12h.getHours() - 12, 0, 0, 0);

    // ── 1. Historical data from HA Recorder (last 12h) ──
    let pvHist = {}, loadHist = {}, socHist = {}, gridHist = {}, battHist = {};
    try {
      const pvEntity = this._m('pv_power') || 'sensor.pv_power';
      const loadEntity = this._m('load_power') || 'sensor.load';
      const socEntity = this._m('battery_soc') || 'sensor.battery_state_of_charge';
      const gridEntity = this._m('grid_power') || 'sensor.meter_active_power_total';
      const battEntity = this._m('battery_power') || 'sensor.battery_power';

      const stats = await this._hass.callWS({
        type: 'recorder/statistics_during_period',
        start_time: start12h.toISOString(),
        end_time: now.toISOString(),
        statistic_ids: [pvEntity, loadEntity, socEntity, gridEntity, battEntity],
        period: 'hour',
        types: ['mean'],
      });

      const parseStats = (arr) => {
        const map = {};
        if (!arr) return map;
        arr.forEach(s => {
          const d = new Date(s.start);
          map[d.getHours()] = s.mean ?? null;
        });
        return map;
      };

      pvHist = parseStats(stats[pvEntity]);
      loadHist = parseStats(stats[loadEntity]);
      socHist = parseStats(stats[socEntity]);
      gridHist = parseStats(stats[gridEntity]);
      battHist = parseStats(stats[battEntity]);
    } catch (e) {
      console.warn('[SH] Forecast chart: Recorder query failed', e);
    }

    // ── 2. Forecast data from Forecast.Solar (via Recorder + live sensors) ──
    // Standard HA Forecast.Solar does NOT expose 'watts' attribute.
    // We use: (a) Recorder history of sensor.power_production_now for past forecast
    //         (b) Remaining energy + bell curve for future hours projection
    let pvForecast = {};
    try {
      const fsPower1 = 'sensor.power_production_now';
      const fsPower2 = 'sensor.power_production_now_2';

      // Current forecast value (predicted power right now)
      const fsNow1 = parseFloat(this._hass.states[fsPower1]?.state) || 0;
      const fsNow2 = parseFloat(this._hass.states[fsPower2]?.state) || 0;
      const fsTotalNow = fsNow1 + fsNow2;

      // Remaining energy today (kWh)
      const remaining1 = parseFloat(this._hass.states['sensor.energy_production_today_remaining']?.state) || 0;
      const remaining2 = parseFloat(this._hass.states['sensor.energy_production_today_remaining_2']?.state) || 0;
      const totalRemainingKwh = remaining1 + remaining2;

      // (a) Historical forecast from Recorder (what FS predicted at each hour)
      const fcStart = new Date(now); fcStart.setHours(fcStart.getHours() - 24, 0, 0, 0);
      const fcStats = await this._hass.callWS({
        type: 'recorder/statistics_during_period',
        start_time: fcStart.toISOString(),
        end_time: now.toISOString(),
        statistic_ids: [fsPower1, fsPower2],
        period: 'hour',
        types: ['mean'],
      });
      (fcStats[fsPower1] || []).forEach(s => {
        const h = new Date(s.start).getHours();
        pvForecast[h] = (pvForecast[h] || 0) + (s.mean ?? 0);
      });
      (fcStats[fsPower2] || []).forEach(s => {
        const h = new Date(s.start).getHours();
        pvForecast[h] = (pvForecast[h] || 0) + (s.mean ?? 0);
      });

      // (b) Future projection using remaining kWh + bell curve
      if (totalRemainingKwh > 0.1) {
        const sunset = 19;
        const hoursLeft = Math.max(1, sunset - nowHour);
        const peakHour = nowHour + Math.floor(hoursLeft / 2);
        for (let h = nowHour + 1; h <= Math.min(sunset + 1, 23); h++) {
          const dist = Math.abs(h - peakHour);
          const sigma = hoursLeft / 2.5;
          const weight = Math.exp(-0.5 * (dist * dist) / (sigma * sigma));
          const estimatedW = Math.round((totalRemainingKwh * 1000 / hoursLeft) * weight);
          pvForecast[h] = Math.max(pvForecast[h] || 0, estimatedW);
        }
      }

      // Set current hour = live value
      pvForecast[nowHour] = fsTotalNow;

      console.log('[SH] Forecast.Solar: now=' + fsTotalNow + 'W, remaining=' + totalRemainingKwh.toFixed(1) + 'kWh, forecast hours:', Object.keys(pvForecast).length);
    } catch (e) {
      console.warn('[SH] Forecast chart: Forecast.Solar fetch failed', e);
    }

    // ── 3. Load profile forecast (7-day average from today's pattern) ──
    let loadForecast = {};
    try {
      // Use 7-day average from Recorder for load profile
      const loadEntity = this._m('load_power') || 'sensor.load';
      const weekAgo = new Date(now); weekAgo.setDate(weekAgo.getDate() - 7); weekAgo.setHours(0, 0, 0, 0);
      const loadStats = await this._hass.callWS({
        type: 'recorder/statistics_during_period',
        start_time: weekAgo.toISOString(),
        end_time: now.toISOString(),
        statistic_ids: [loadEntity],
        period: 'hour',
        types: ['mean'],
      });
      const arr = loadStats[loadEntity] || [];
      const hourSums = {}; const hourCounts = {};
      arr.forEach(s => {
        const h = new Date(s.start).getHours();
        hourSums[h] = (hourSums[h] || 0) + (s.mean || 0);
        hourCounts[h] = (hourCounts[h] || 0) + 1;
      });
      for (let h = 0; h < 24; h++) {
        if (hourCounts[h]) loadForecast[h] = Math.round(hourSums[h] / hourCounts[h]);
      }
    } catch (e) {
      console.warn('[SH] Forecast chart: Load profile fetch failed', e);
    }

    // ── 4. Assemble unified timeline ──
    const startHour = (nowHour - 12 + 24) % 24;
    const points = [];
    for (let i = 0; i < 25; i++) {
      const h = (startHour + i) % 24;
      const isPast = i < 12;
      const isNow = i === 12;
      const isFuture = i > 12;
      points.push({
        hour: h,
        label: `${String(h).padStart(2, '0')}:00`,
        offsetIdx: i,
        isPast, isNow, isFuture,
        pvActual: isPast || isNow ? (pvHist[h] ?? null) : null,
        pvForecast: isFuture || isNow ? (pvForecast[h] ?? null) : (isPast && pvForecast[h] !== undefined ? pvForecast[h] : null),
        loadActual: isPast || isNow ? (loadHist[h] ?? null) : null,
        loadForecast: isFuture || isNow ? (loadForecast[h] ?? null) : null,
        socActual: isPast || isNow ? (socHist[h] ?? null) : null,
        gridActual: isPast || isNow ? (gridHist[h] ?? null) : null,
        battActual: isPast || isNow ? (battHist[h] ?? null) : null,
      });
    }

    // Inject live values at "now" point
    const nowPoint = points[12];
    nowPoint.pvActual = this._nm('pv_power') ?? nowPoint.pvActual;
    nowPoint.loadActual = this._nm('load_power') ?? nowPoint.loadActual;
    nowPoint.socActual = this._nm('battery_soc') ?? nowPoint.socActual;
    nowPoint.gridActual = this._nm('grid_power') ?? nowPoint.gridActual;
    nowPoint.battActual = this._nm('battery_power') ?? nowPoint.battActual;

    this._fcChartCache = points;
    this._fcChartCacheTs = Date.now();
    return points;
  }

  _renderForecastChart(containerId, points, config) {
    const container = this.shadowRoot.getElementById(containerId);
    if (!container || !points || points.length === 0) return;

    const { actualKey, forecastKey, actualColor, forecastColor, label, unit, yMin: cfgYMin, yMax: cfgYMax, height: cfgHeight, isMini } = config;
    const containerW = container.clientWidth || (isMini ? 700 : 900);
    const W = Math.max(containerW, isMini ? 500 : 600);
    const H = cfgHeight || (isMini ? 130 : 240);
    const padL = isMini ? 30 : 55, padR = 20, padT = 18, padB = isMini ? 20 : 35;
    const chartW = W - padL - padR;
    const chartH = H - padT - padB;

    // Extract values
    const actualVals = points.map(p => p[actualKey]);
    const forecastVals = points.map(p => p[forecastKey]);
    const allVals = [...actualVals, ...forecastVals].filter(v => v !== null && v !== undefined && !isNaN(v));
    if (allVals.length === 0) {
      container.innerHTML = `<div style="text-align:center; color:#475569; font-size:11px; padding:20px">⏳ Zbieranie danych historycznych...</div>`;
      return;
    }

    const dataMin = Math.min(...allVals);
    const dataMax = Math.max(...allVals);
    const dataRange = dataMax - dataMin;
    const yMin = cfgYMin !== undefined ? cfgYMin : (dataMin < 0 ? dataMin - dataRange * 0.1 : Math.max(0, dataMin - dataRange * 0.1));
    const yMax = cfgYMax !== undefined ? cfgYMax : Math.max(dataMax + dataRange * 0.15, dataMax + 50);
    const yRange = Math.max(yMax - yMin, 1);

    const xStep = chartW / (points.length - 1);
    const nowIdx = 12;

    // Build SVG polylines
    const toX = (i) => padL + i * xStep;
    const toY = (v) => padT + chartH - ((v - yMin) / yRange) * chartH;

    const buildPath = (vals) => {
      let d = '';
      let started = false;
      vals.forEach((v, i) => {
        if (v === null || v === undefined || isNaN(v)) return;
        const x = toX(i).toFixed(1);
        const y = toY(v).toFixed(1);
        d += started ? ` L${x},${y}` : `M${x},${y}`;
        started = true;
      });
      return d;
    };

    const buildAreaPath = (vals) => {
      let coords = [];
      vals.forEach((v, i) => {
        if (v === null || v === undefined || isNaN(v)) return;
        coords.push({ x: toX(i), y: toY(v), i });
      });
      if (coords.length < 2) return '';
      let d = `M${coords[0].x.toFixed(1)},${(padT + chartH).toFixed(1)}`;
      coords.forEach(c => { d += ` L${c.x.toFixed(1)},${c.y.toFixed(1)}`; });
      d += ` L${coords[coords.length - 1].x.toFixed(1)},${(padT + chartH).toFixed(1)} Z`;
      return d;
    };

    const actualPath = buildPath(actualVals);
    const forecastPath = buildPath(forecastVals);
    const actualArea = buildAreaPath(actualVals);
    const forecastArea = buildAreaPath(forecastVals);

    // Grid lines
    const gridLines = [];
    const ySteps = isMini ? 3 : 5;
    for (let i = 0; i <= ySteps; i++) {
      const v = yMin + (yRange / ySteps) * i;
      const y = toY(v).toFixed(1);
      gridLines.push(`<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="rgba(255,255,255,0.04)" stroke-width="0.5" />`);
      if (!isMini || i % 2 === 0) {
        const lbl = v >= 1000 ? `${(v / 1000).toFixed(1)}k` : Math.round(v);
        gridLines.push(`<text x="${padL - 4}" y="${y}" text-anchor="end" dominant-baseline="middle" fill="#475569" font-size="8" font-family="Inter,system-ui,sans-serif">${lbl}</text>`);
      }
    }

    // X labels (every 3h)
    const xLabels = [];
    points.forEach((p, i) => {
      if (i % 3 === 0 || i === nowIdx) {
        const x = toX(i).toFixed(1);
        const isN = i === nowIdx;
        xLabels.push(`<text x="${x}" y="${H - 3}" text-anchor="middle" fill="${isN ? '#00d4ff' : '#475569'}" font-size="${isN ? 9 : 7}" font-weight="${isN ? 800 : 400}" font-family="Inter,system-ui,sans-serif">${isN ? 'TERAZ' : p.label}</text>`);
      }
    });

    // Now line
    const nowX = toX(nowIdx).toFixed(1);
    const nowLine = `
      <line x1="${nowX}" y1="${padT}" x2="${nowX}" y2="${padT + chartH}" stroke="rgba(0,212,255,0.4)" stroke-width="1" stroke-dasharray="4,3" style="animation:shNowPulse 2s ease-in-out infinite" />
      <circle cx="${nowX}" cy="${padT - 1}" r="3" fill="#00d4ff" style="filter:drop-shadow(0 0 4px #00d4ff)" />
    `;

    // Interactive overlay rects for tooltip
    const hitAreas = points.map((p, i) => {
      const x = toX(i) - xStep / 2;
      return `<rect x="${Math.max(padL, x).toFixed(1)}" y="${padT}" width="${xStep.toFixed(1)}" height="${chartH}" fill="transparent" data-idx="${i}" class="sh-chart-hit" />`;
    }).join('');

    // Unique IDs
    const uid = containerId.replace(/[^a-z0-9]/gi, '');

    const svg = `
      <svg class="sh-chart-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
        <defs>
          <linearGradient id="grad-a-${uid}" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="${actualColor}" stop-opacity="0.25" />
            <stop offset="100%" stop-color="${actualColor}" stop-opacity="0.02" />
          </linearGradient>
          <linearGradient id="grad-f-${uid}" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="${forecastColor}" stop-opacity="0.15" />
            <stop offset="100%" stop-color="${forecastColor}" stop-opacity="0.01" />
          </linearGradient>
        </defs>
        ${gridLines.join('')}
        ${xLabels.join('')}
        <!-- Area fills -->
        <path d="${actualArea}" fill="url(#grad-a-${uid})" />
        <path d="${forecastArea}" fill="url(#grad-f-${uid})" />
        <!-- Lines -->
        <path d="${actualPath}" fill="none" stroke="${actualColor}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        <path d="${forecastPath}" fill="none" stroke="${forecastColor}" stroke-width="1.5" stroke-dasharray="6,4" stroke-linecap="round" />
        ${yMin < 0 ? `<line x1="${padL}" y1="${toY(0).toFixed(1)}" x2="${padL + chartW}" y2="${toY(0).toFixed(1)}" stroke="rgba(255,255,255,0.2)" stroke-width="1" stroke-dasharray="3,3" /><text x="${padL - 5}" y="${toY(0).toFixed(1)}" fill="rgba(255,255,255,0.35)" font-size="9" text-anchor="end" dominant-baseline="middle">0</text>` : ''}
        ${nowLine}
        <!-- Data points on actual line -->
        ${actualVals.map((v, i) => v !== null && v !== undefined ? `<circle cx="${toX(i).toFixed(1)}" cy="${toY(v).toFixed(1)}" r="${i === nowIdx ? 4 : 2}" fill="${actualColor}" stroke="${i === nowIdx ? '#fff' : 'none'}" stroke-width="${i === nowIdx ? 1 : 0}" style="${i === nowIdx ? 'filter:drop-shadow(0 0 6px ' + actualColor + ')' : ''}" />` : '').join('')}
        ${hitAreas}
      </svg>
      <div class="sh-chart-tooltip" id="tt-${uid}"></div>
    `;

    container.innerHTML = svg;

    // Tooltip handlers
    const tooltip = container.querySelector(`#tt-${uid}`);
    container.querySelectorAll('.sh-chart-hit').forEach(rect => {
      rect.addEventListener('mouseenter', (e) => {
        const idx = parseInt(e.target.dataset.idx);
        const p = points[idx];
        if (!p) return;
        const av = p[actualKey];
        const fv = p[forecastKey];
        if (av === null && fv === null) return;
        let html = `<div style="font-weight:700; margin-bottom:3px; color:#fff">${p.label}${p.isNow ? ' (teraz)' : ''}</div>`;
        if (av !== null && av !== undefined) {
          const avFmt = Math.abs(av) >= 1000 ? `${(av / 1000).toFixed(1)}k` : Math.round(av);
          html += `<div class="sh-chart-tooltip-row"><div class="sh-chart-tooltip-dot" style="background:${actualColor}"></div>Rzeczywiste: <strong>${avFmt} ${unit}</strong></div>`;
        }
        if (fv !== null && fv !== undefined) {
          const fvFmt = Math.abs(fv) >= 1000 ? `${(fv / 1000).toFixed(1)}k` : Math.round(fv);
          html += `<div class="sh-chart-tooltip-row"><div class="sh-chart-tooltip-dot" style="background:${forecastColor}"></div>Prognoza: <strong>${fvFmt} ${unit}</strong></div>`;
        }
        if (av !== null && fv !== null && av !== undefined && fv !== undefined && fv > 0) {
          const diff = ((av / fv) * 100).toFixed(0);
          html += `<div style="font-size:9px; color:#64748b; margin-top:2px">Celność: ${diff}%</div>`;
        }
        tooltip.innerHTML = html;
        tooltip.style.display = 'block';
        // Measure tooltip height first (place off-screen briefly)
        tooltip.style.left = '-9999px'; tooltip.style.top = '0';
        tooltip.style.transform = 'none';
        const tooltipH = tooltip.offsetHeight || 60;
        const tooltipW = tooltip.offsetWidth || 120;
        const rect2 = e.target.getBoundingClientRect();
        const containerRect = container.getBoundingClientRect();
        let tooltipX = rect2.left + rect2.width / 2 - containerRect.left;
        let tooltipY = rect2.top - containerRect.top - 10;
        // Flip below if tooltip would go above container
        if (tooltipY - tooltipH < -5) {
          tooltipY = rect2.bottom - containerRect.top + 10;
          tooltip.style.transform = 'translateX(-50%)';
        } else {
          tooltip.style.transform = 'translateX(-50%) translateY(-100%)';
        }
        // Clamp horizontal position
        tooltipX = Math.max(tooltipW / 2 + 4, Math.min(tooltipX, containerRect.width - tooltipW / 2 - 4));
        tooltip.style.left = `${tooltipX}px`;
        tooltip.style.top = `${tooltipY}px`;
      });
      rect.addEventListener('mouseleave', () => {
        tooltip.style.display = 'none';
      });
    });
  }

  async _updateForecastCharts() {
    if (!this._hass) return;
    const tab = this._activeTab;

    // Only fetch data when relevant tab is active
    if (tab !== 'energy' && tab !== 'battery' && tab !== 'overview') return;

    try {
      const points = await this._fetchForecastChartData();
      if (!points) {
        console.warn('[SH] Forecast charts: no data returned from _fetchForecastChartData');
        return;
      }

      if (tab === 'energy') {
        // PV Production chart
        this._renderForecastChart('sh-chart-pv', points, {
          actualKey: 'pvActual', forecastKey: 'pvForecast',
          actualColor: '#f7b731', forecastColor: '#a855f7',
          label: 'Produkcja PV', unit: 'W',
        });

        // Load consumption chart
        this._renderForecastChart('sh-chart-load', points, {
          actualKey: 'loadActual', forecastKey: 'loadForecast',
          actualColor: '#2ecc71', forecastColor: '#00d4ff',
          label: 'Zużycie domu', unit: 'W',
        });
      }

      if (tab === 'battery') {
        // SOC chart
        this._renderForecastChart('sh-chart-soc', points, {
          actualKey: 'socActual', forecastKey: null,
          actualColor: '#00d4ff', forecastColor: '#475569',
          label: 'SOC Baterii', unit: '%',
          yMin: 0, yMax: 100,
        });

        // Battery Power chart
        this._renderForecastChart('sh-chart-batt-power', points, {
          actualKey: 'battActual', forecastKey: null,
          actualColor: '#a855f7', forecastColor: '#475569',
          label: 'Moc baterii', unit: 'W',
        });
      }

      if (tab === 'overview') {
        // Mini overview chart — PV only
        this._renderForecastChart('sh-chart-ov-mini', points, {
          actualKey: 'pvActual', forecastKey: 'pvForecast',
          actualColor: '#f7b731', forecastColor: '#a855f7',
          label: 'PV', unit: 'W',
          height: 90, isMini: true,
        });
      }
    } catch (err) {
      console.error('[SH] Forecast charts error:', err);
    }
  }

  /* ── AI ROI Interpreter — trigger, display and event handling ── */

  _triggerRoiAiAnalysis() {
    if (!this._roiDataForAI || !this._roiDataForAI.length) {
      const st = this.shadowRoot.getElementById('roi-ai-status');
      if (st) { st.textContent = '❌ Brak danych ROI — odśwież stronę'; st.style.color = '#e74c3c'; }
      return;
    }

    // Set loading flag — survives re-renders
    this._roiAiLoading = true;

    const btn = this.shadowRoot.getElementById('btn-roi-ai-analyze');
    const status = this.shadowRoot.getElementById('roi-ai-status');
    const result = this.shadowRoot.getElementById('roi-ai-result');

    if (btn) {
      btn.disabled = true;
      btn.style.opacity = '0.5';
      btn.innerHTML = '<span style="animation:pulse 1s infinite">⏳</span> Analizuję...';
    }
    if (status) {
      status.textContent = '🧠 AI analizuje dane ROI — to może potrwać do 30 sekund...';
      status.style.color = '#00d4ff';
    }
    if (result) result.style.display = 'none';

    // Send ROI data to backend AI service
    const roiPayload = JSON.stringify(this._roiDataForAI);
    this._hass.callService('smartinghome', 'analyze_roi', {
      roi_data: roiPayload,
    }).catch(err => {
      this._roiAiLoading = false;
      if (status) { status.textContent = `❌ Błąd: ${err.message || err}`; status.style.color = '#e74c3c'; }
      if (btn) { btn.disabled = false; btn.style.opacity = '1'; btn.innerHTML = '🧠 Analizuj z AI'; }
    });
  }

  _displayRoiAiResult(data) {
    // Clear loading flag
    this._roiAiLoading = false;

    const btn = this.shadowRoot.getElementById('btn-roi-ai-analyze');
    const status = this.shadowRoot.getElementById('roi-ai-status');
    const result = this.shadowRoot.getElementById('roi-ai-result');

    if (data.error) {
      if (status) { status.textContent = `❌ ${data.error}`; status.style.color = '#e74c3c'; }
      if (btn) { btn.disabled = false; btn.style.opacity = '1'; btn.innerHTML = '🧠 Analizuj z AI'; }
      return;
    }

    if (data.text) {
      // Cache result for re-render survival
      this._roiAiCachedResult = data;
      this._roiAiRawMarkdown = data.text;

      if (result) {
        // Render markdown with existing _renderMarkdown method
        const html = this._renderMarkdown ? this._renderMarkdown(data.text) : data.text;
        const provIcon = data.provider === 'anthropic' ? '🟣' : '🔵';
        const provName = data.provider === 'anthropic' ? 'Claude' : 'Gemini';
        result.innerHTML = `
          <div style="display:flex; align-items:center; gap:6px; margin-bottom:8px; padding-bottom:6px; border-bottom:1px solid rgba(255,255,255,0.06)">
            <span style="font-size:14px">🧠</span>
            <span style="font-size:10px; font-weight:700; color:#00d4ff">Interpretacja AI</span>
            <span style="font-size:8px; color:#64748b; margin-left:auto">${provIcon} ${provName} · ${data.timestamp || ''}</span>
          </div>
          <div style="font-size:11px; color:#e2e8f0; line-height:1.7">${html}</div>
          <div style="display:flex; gap:6px; margin-top:10px; padding-top:8px; border-top:1px solid rgba(255,255,255,0.06)">
            <button id="btn-roi-copy-text" onclick="this.getRootNode().host._copyRoiAiResult('text', this)"
              style="flex:1; padding:6px 10px; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.1); border-radius:6px; color:#94a3b8; font-size:9px; cursor:pointer; transition:all 0.3s">
              📋 Kopiuj tekst
            </button>
            <button id="btn-roi-copy-md" onclick="this.getRootNode().host._copyRoiAiResult('markdown', this)"
              style="flex:1; padding:6px 10px; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.1); border-radius:6px; color:#94a3b8; font-size:9px; cursor:pointer; transition:all 0.3s">
              📝 Kopiuj markdown
            </button>
          </div>
        `;
        result.style.display = 'block';
      }
    }

    if (status) {
      const now = new Date().toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit' });
      status.textContent = `✅ Analiza gotowa (${now})`;
      status.style.color = '#2ecc71';
    }
    if (btn) {
      btn.disabled = false;
      btn.style.opacity = '1';
      btn.innerHTML = '🔄 Analizuj ponownie';
    }
  }

  _copyRoiAiResult(format, btnEl) {
    if (!this._roiAiRawMarkdown) return;

    let textToCopy;
    if (format === 'markdown') {
      textToCopy = this._roiAiRawMarkdown;
    } else {
      // Convert markdown to clean plain text
      textToCopy = this._roiAiRawMarkdown
        .replace(/#{1,6}\s*/g, '')       // remove headers
        .replace(/\*\*(.*?)\*\*/g, '$1') // remove bold
        .replace(/\*(.*?)\*/g, '$1')     // remove italic
        .replace(/`(.*?)`/g, '$1')       // remove code
        .replace(/^\s*[-●•]\s*/gm, '• ') // normalize bullets
        .replace(/^\s*>\s*/gm, '')       // remove blockquotes
        .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1') // links → text
        .replace(/---/g, '—————')        // horizontal rules
        .replace(/\n{3,}/g, '\n\n')      // collapse blank lines
        .trim();
    }

    const origText = btnEl ? btnEl.innerHTML : '';
    const origBg = btnEl ? btnEl.style.background : '';
    const origColor = btnEl ? btnEl.style.color : '';
    const origBorder = btnEl ? btnEl.style.borderColor : '';

    navigator.clipboard.writeText(textToCopy).then(() => {
      // Visual feedback directly on the button
      if (btnEl) {
        btnEl.innerHTML = '✅ Skopiowano!';
        btnEl.style.background = 'rgba(46,204,113,0.15)';
        btnEl.style.color = '#2ecc71';
        btnEl.style.borderColor = 'rgba(46,204,113,0.4)';
        setTimeout(() => {
          btnEl.innerHTML = origText;
          btnEl.style.background = origBg;
          btnEl.style.color = origColor;
          btnEl.style.borderColor = origBorder;
        }, 2000);
      }
    }).catch(err => {
      console.error('[SH] Copy failed:', err);
      if (btnEl) {
        btnEl.innerHTML = '❌ Błąd kopiowania';
        btnEl.style.background = 'rgba(231,76,60,0.15)';
        btnEl.style.color = '#e74c3c';
        setTimeout(() => {
          btnEl.innerHTML = origText;
          btnEl.style.background = origBg;
          btnEl.style.color = origColor;
          btnEl.style.borderColor = origBorder;
        }, 2000);
      }
    });
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
      if (this._roiSub) { try { this._roiSub(); } catch(e) {} this._roiSub = null; }
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
    // Subscribe to ROI AI analysis results
    if (!this._roiSub) {
      try {
        this._roiSub = conn.subscribeEvents((ev) => {
          this._displayRoiAiResult(ev.data);
          // Also save to settings cache
          if (ev.data.text) {
            this._settings.ai_roi_analysis = ev.data;
          }
        }, "smartinghome_roi_analysis");
        if (this._roiSub && typeof this._roiSub.then === 'function') {
          this._roiSub.then(unsub => { this._roiSub = unsub; }).catch(err => {
            console.warn('[SH] Failed to subscribe to ROI analysis:', err);
            this._roiSub = null;
          });
        }
      } catch(e) { console.warn('[SH] subscribeEvents ROI error:', e); this._roiSub = null; }
    }
  }

  /* ═══════════════════════════════════════════════════════════ */
  /* ═══  ALERTS & ANOMALIES ENGINE                        ═══ */
  /* ═══════════════════════════════════════════════════════════ */

  _updateAlertsTab() {
    if (!this._hass) return;
    // Tier gate — alerts only for PRO/ENTERPRISE
    const tier = this._tier();
    if (tier !== 'PRO' && tier !== 'ENTERPRISE') return;
    // Initialize alert state
    if (!this._alertState) {
      this._alertState = { alerts: [], history: [], lastSensorTs: {}, acknowledgedIds: new Set() };
      // Restore history from settings
      if (this._settings.alert_history) {
        this._alertState.history = this._settings.alert_history.slice(-50);
      }
    }

    // Run anomaly engine
    const alerts = this._runAnomalyEngine();
    this._alertState.alerts = alerts;

    // Persist new alerts to history (dedup by id + 5min window)
    const now = Date.now();
    alerts.forEach(a => {
      const isDup = this._alertState.history.some(h =>
        h.id === a.id && (now - h.ts) < 300000
      );
      if (!isDup) {
        this._alertState.history.push({ ...a, ts: now });
        // Send notification for new alerts
        this._sendAlertNotification(a);
      }
    });
    // Trim history to last 50
    if (this._alertState.history.length > 50) {
      this._alertState.history = this._alertState.history.slice(-50);
    }

    // Calculate health score
    const health = this._calcHealthScore(alerts);

    // Render all sections
    this._renderAlertStatusBar(alerts, health);
    this._renderAlertTiles();
    this._renderActiveAlerts(alerts);
    this._renderHealthBreakdown(health);
    this._renderAlertHistory();
    this._renderNotificationLog();

    // Save history periodically (every 60s)
    if (!this._lastAlertHistorySave || (now - this._lastAlertHistorySave) > 60000) {
      this._lastAlertHistorySave = now;
      const last24h = this._alertState.history.filter(h => (now - h.ts) < 86400000);
      this._savePanelSettings({ alert_history: last24h.slice(-50) });
    }
  }

  _runAnomalyEngine() {
    const alerts = [];
    const hour = new Date().getHours();
    const isDaytime = hour >= 6 && hour <= 21;
    const isSolarHours = hour >= 8 && hour <= 17;

    // ─── Layer 1: Hard Alerts (device data) ───
    this._checkHardAlerts(alerts, isDaytime, isSolarHours);

    // ─── Layer 2: Soft Anomalies (behavioral) ───
    this._checkSoftAnomalies(alerts, isDaytime, isSolarHours);

    // ─── Layer 3: Risk Predictions ───
    this._checkRiskPredictions(alerts, isDaytime);

    return alerts;
  }

  _checkHardAlerts(alerts, isDaytime, isSolarHours) {
    // Inverter offline (daytime only)
    const pvPower = this._nm('pv_power');
    const invTemp = this._nm('inverter_temp');
    const loadPower = this._nm('load_power');
    const batSoc = this._nm('battery_soc');

    if (isDaytime && pvPower === null && loadPower === null) {
      alerts.push({
        id: 'INV_OFFLINE', level: 'critical', source: 'Falownik',
        title: 'Falownik offline',
        desc: 'Brak odczytu danych z falownika — sprawdź zasilanie i komunikację',
        diag: {
          detected: 'Brak danych z sensorów PV i Load',
          reason: 'Falownik nie odpowiada na zapytania Modbus/LAN',
          causes: ['Brak zasilania falownika', 'Awaria komunikacji RS485/LAN', 'Restart falownika', 'Uszkodzenie loggera WiFi'],
          action: 'Sprawdź zasilanie falownika, kabel RS485, status loggera WiFi',
          severity: 'Wymaga natychmiastowej interwencji'
        }
      });
    }

    // Inverter overtemperature
    if (invTemp !== null) {
      if (invTemp > 75) {
        alerts.push({
          id: 'INV_OVERTEMP_CRIT', level: 'critical', source: 'Falownik',
          title: `Przegrzanie falownika: ${invTemp.toFixed(1)}°C`,
          desc: 'Temperatura krytyczna — falownik może się wyłączyć automatycznie',
          diag: {
            detected: `Temperatura falownika: ${invTemp.toFixed(1)}°C (próg: 75°C)`,
            reason: 'Przegrzanie wewnętrzne falownika',
            causes: ['Zablokowana wentylacja', 'Wysoka temp. otoczenia', 'Przeciążenie', 'Uszkodzony wentylator'],
            action: 'Sprawdź wentylację falownika, wyczyść filtry, zmniejsz obciążenie',
            severity: 'Krytyczne — ryzyko wyłączenia'
          }
        });
      } else if (invTemp > 65) {
        alerts.push({
          id: 'INV_OVERTEMP', level: 'warning', source: 'Falownik',
          title: `Wysoka temperatura falownika: ${invTemp.toFixed(1)}°C`,
          desc: 'Temperatura powyżej normy — monitoruj sytuację',
          diag: {
            detected: `Temperatura: ${invTemp.toFixed(1)}°C (próg ostrzegawczy: 65°C)`,
            reason: 'Podwyższona temperatura pracy',
            causes: ['Słaba wentylacja', 'Duże obciążenie', 'Wysoka temp. otoczenia (lato)'],
            action: 'Zapewnij lepszą wentylację, sprawdź filtry powietrza',
            severity: 'Ostrzeżenie — obserwuj trend'
          }
        });
      }
    }

    // Battery BMS offline
    if (batSoc === null) {
      alerts.push({
        id: 'BAT_BMS_OFFLINE', level: 'critical', source: 'Bateria',
        title: 'Brak danych baterii / BMS offline',
        desc: 'Brak odczytu SOC — możliwa utrata komunikacji z BMS',
        diag: {
          detected: 'Sensor battery_soc zwraca null',
          reason: 'Brak komunikacji z systemem zarządzania baterią (BMS)',
          causes: ['Awaria CAN/RS485 baterii', 'Restart BMS', 'Luźny kabel komunikacyjny', 'Bateria wyłączona'],
          action: 'Sprawdź kabel komunikacyjny baterii, napięcie baterii, status falownika',
          severity: 'Krytyczne — brak kontroli nad baterią'
        }
      });
    }

    // Grid overvoltage
    const vl1 = this._nm('voltage_l1');
    const vl2 = this._nm('voltage_l2');
    const vl3 = this._nm('voltage_l3');
    const voltages = [vl1, vl2, vl3].filter(v => v !== null);

    voltages.forEach((v, i) => {
      const phase = `L${i + 1}`;
      if (v > 253) {
        alerts.push({
          id: `GRID_OVERVOLT_${phase}`, level: 'critical', source: 'Sieć',
          title: `Napięcie ${phase} krytycznie wysokie: ${v.toFixed(1)}V`,
          desc: 'Przekroczone dopuszczalne napięcie sieci — falownik może się wyłączyć',
          diag: {
            detected: `${phase}: ${v.toFixed(1)}V (próg: 253V)`,
            reason: 'Napięcie sieci przekracza normę',
            causes: ['Problemy z siecią energetyczną', 'Zbyt dużo eksportu PV w okolicy', 'Słaby transformator'],
            action: 'Ogranicz eksport do sieci, włącz zero-export, zgłoś do operatora',
            severity: 'Krytyczne — ryzyko odstawienia falownika'
          }
        });
      } else if (v > 245) {
        alerts.push({
          id: `GRID_HIGHVOLT_${phase}`, level: 'warning', source: 'Sieć',
          title: `Wysokie napięcie ${phase}: ${v.toFixed(1)}V`,
          desc: 'Napięcie bliskie limitu — monitoruj',
          diag: {
            detected: `${phase}: ${v.toFixed(1)}V (próg ostrzegawczy: 245V)`,
            reason: 'Napięcie sieci powyżej komfortowej normy',
            causes: ['Duży eksport PV w regionie', 'Niska konsumpcja w sieci', 'Problemy po stronie operatora'],
            action: 'Rozważ ograniczenie eksportu, obserwuj trend',
            severity: 'Ostrzeżenie'
          }
        });
      }
    });

    // Grid frequency drift
    const gridFreq = this._nm('grid_frequency');
    if (gridFreq !== null && (gridFreq < 49.5 || gridFreq > 50.5)) {
      alerts.push({
        id: 'GRID_FREQ_DRIFT', level: 'warning', source: 'Sieć',
        title: `Częstotliwość sieci: ${gridFreq.toFixed(2)} Hz`,
        desc: `Poza normą 49.5-50.5 Hz — niestabilność sieci`,
        diag: {
          detected: `Częstotliwość: ${gridFreq.toFixed(2)} Hz`,
          reason: 'Częstotliwość sieci odbiega od normy 50 Hz',
          causes: ['Niestabilność sieci energetycznej', 'Przeciążenie sieci', 'Awaria regionalna'],
          action: 'Obserwuj, jeśli utrzymuje się — zgłoś operatorowi',
          severity: 'Ostrzeżenie'
        }
      });
    }
  }

  _checkSoftAnomalies(alerts, isDaytime, isSolarHours) {
    const pvPower = this._nm('pv_power') || 0;
    const pv1 = this._nm('pv1_power');
    const pv2 = this._nm('pv2_power');
    const batPower = this._nm('battery_power') || 0;
    const batSoc = this._nm('battery_soc');
    const gridPower = this._nm('grid_power') || 0;
    const loadPower = this._nm('load_power') || 0;

    // PV underperformance (solar hours only, needs irradiance or forecast)
    if (isSolarHours && pvPower !== null) {
      // Use forecast as expected if available
      const forecastState = this._hass?.states?.['sensor.smartinghome_pv_forecast_power_now_total'];
      const expectedPv = forecastState ? parseFloat(forecastState.state) : null;

      if (expectedPv && expectedPv > 500 && pvPower < expectedPv * 0.65) {
        const deficit = Math.round((1 - pvPower / expectedPv) * 100);
        alerts.push({
          id: 'PV_UNDERPERFORM', level: 'warning', source: 'PV',
          title: `PV poniżej oczekiwań: -${deficit}%`,
          desc: `Aktualna: ${this._pw(pvPower)}, oczekiwana: ${this._pw(expectedPv)}`,
          diag: {
            detected: `Produkcja PV ${deficit}% poniżej prognozy`,
            reason: 'Rzeczywista produkcja znacząco niższa od oczekiwanej',
            causes: ['Zabrudzenie paneli', 'Częściowe zacienienie', 'Uszkodzony string', 'Degradacja paneli', 'Zachmurzenie (jeśli prognoza niedokładna)'],
            action: 'Sprawdź panele wizualnie, porównaj stringi, wyczyść panele',
            severity: 'Ważne — potencjalna utrata produkcji'
          }
        });
      }
    }

    // PV string anomaly — per-string baseline tracking
    // Instead of comparing PV1 vs PV2 (which can differ by design),
    // we track each string's own historical performance and detect
    // when it drops significantly below its own baseline at the same hour.
    if (isSolarHours && (pv1 !== null || pv2 !== null)) {
      this._trackAndCheckStringBaseline('PV1', pv1, alerts);
      this._trackAndCheckStringBaseline('PV2', pv2, alerts);
    }

    // Battery no charge despite surplus
    if (isSolarHours && pvPower > 1500 && loadPower > 0) {
      const surplus = pvPower - loadPower;
      if (surplus > 500 && batSoc !== null && batSoc < 95 && Math.abs(batPower) < 100) {
        alerts.push({
          id: 'BAT_NO_CHARGE', level: 'warning', source: 'Bateria',
          title: 'Brak ładowania mimo nadwyżki PV',
          desc: `Nadwyżka: ${this._pw(surplus)}, bateria: ${batSoc.toFixed(0)}%, brak ładowania`,
          diag: {
            detected: `Nadwyżka PV ${this._pw(surplus)} przy SOC ${batSoc.toFixed(0)}%, battery_power ≈ 0`,
            reason: 'Bateria nie ładuje się mimo dostępnej energii',
            causes: ['Tryb falownika blokuje ładowanie', 'Bateria w hold mode', 'Limit DOD', 'Awaria BMS', 'Ręczny override HEMS'],
            action: 'Sprawdź tryb pracy falownika, ustawienia DOD, status BMS',
            severity: 'Ważne — marnowanie nadwyżki PV'
          }
        });
      }
    }

    // Grid import with significant PV (CT wiring issue?)
    if (isSolarHours && pvPower > 1000 && gridPower > 500 && batSoc !== null && batSoc < 95) {
      alerts.push({
        id: 'GRID_IMPORT_WITH_PV', level: 'info', source: 'Meter/CT',
        title: 'Import z sieci przy dużej produkcji PV',
        desc: `PV: ${this._pw(pvPower)}, import: ${this._pw(gridPower)}`,
        diag: {
          detected: `Import ${this._pw(gridPower)} mimo produkcji PV ${this._pw(pvPower)}`,
          reason: 'Import z sieci w sytuacji gdy PV powinno pokrywać zapotrzebowanie',
          causes: ['Duże obciążenie domu > PV', 'Niewłaściwy kierunek CT', 'Problem z konfiguracją metera', 'Chwilowy peak zużycia'],
          action: 'Sprawdź obciążenie domu, kierunek przekładników CT',
          severity: 'Informacyjne — sprawdź CT jeśli się powtarza'
        }
      });
    }

    // Export without PV (possible CT error)
    if (pvPower < 100 && gridPower < -200) {
      alerts.push({
        id: 'EXPORT_NO_PV', level: 'warning', source: 'Meter/CT',
        title: 'Eksport do sieci bez produkcji PV',
        desc: `PV: ${this._pw(pvPower)}, eksport: ${this._pw(Math.abs(gridPower))}`,
        diag: {
          detected: `Eksport ${this._pw(Math.abs(gridPower))} przy PV ${this._pw(pvPower)}`,
          reason: 'System raportuje eksport mimo braku produkcji PV — prawdopodobny błąd pomiaru',
          causes: ['Odwrócony kierunek CT', 'Uszkodzony przekładnik prądowy', 'Błąd kalibracji metera'],
          action: 'Sprawdź kierunek montażu przekładników CT, zamień fazy',
          severity: 'Ważne — błędny pomiar = złe decyzje HEMS'
        }
      });
    }

    // Phase imbalance
    const pl1 = this._nm('power_l1') || 0;
    const pl2 = this._nm('power_l2') || 0;
    const pl3 = this._nm('power_l3') || 0;
    const phases = [Math.abs(pl1), Math.abs(pl2), Math.abs(pl3)].filter(v => v > 0);
    if (phases.length >= 2) {
      const phaseDelta = Math.max(...phases) - Math.min(...phases);
      if (phaseDelta > 3000) {
        alerts.push({
          id: 'PHASE_IMBALANCE', level: 'warning', source: 'Sieć',
          title: `Nierównomierność faz: Δ ${this._pw(phaseDelta)}`,
          desc: `L1: ${this._pw(pl1)}, L2: ${this._pw(pl2)}, L3: ${this._pw(pl3)}`,
          diag: {
            detected: `Różnica obciążenia faz: ${this._pw(phaseDelta)} (próg: 3000W)`,
            reason: 'Duża asymetria obciążeń między fazami',
            causes: ['Duże odbiorniki na jednej fazie', 'Niewłaściwy podział obwodów', 'Uruchomienie dużego odbiornika 1-fazowego'],
            action: 'Rozważ przeniesienie odbiorników na mniej obciążone fazy',
            severity: 'Informacyjne'
          }
        });
      }
    }

    // PV zero during solar hours (with positive irradiance)
    if (isSolarHours && pvPower === 0) {
      // Check if we have irradiance data
      const irradiance = this._hass?.states?.[this._m('local_solar_radiation')];
      const irrVal = irradiance ? parseFloat(irradiance.state) : null;
      if (irrVal !== null && irrVal > 100) {
        alerts.push({
          id: 'PV_MIDDAY_ZERO', level: 'critical', source: 'PV',
          title: 'Brak produkcji PV w dzień',
          desc: `PV = 0W przy nasłonecznieniu ${irrVal.toFixed(0)} W/m²`,
          diag: {
            detected: `Produkcja PV = 0W, nasłonecznienie = ${irrVal.toFixed(0)} W/m²`,
            reason: 'Panele nie produkują energii mimo dobrego nasłonecznienia',
            causes: ['Falownik w trybie standby', 'Wyłącznik DC wyłączony', 'Awaria falownika', 'Izolacja / ground fault', 'Uszkodzenie okablowania DC'],
            action: 'Sprawdź wyłącznik DC, status falownika, logi błędów',
            severity: 'Krytyczne — pełna utrata produkcji'
          }
        });
      }
    }
  }

  _checkRiskPredictions(alerts, isDaytime) {
    const invTemp = this._nm('inverter_temp');
    const batSoc = this._nm('battery_soc');
    const hour = new Date().getHours();

    // Risk: overheating trend
    if (invTemp !== null && invTemp > 55 && isDaytime) {
      // Store temp history for trend
      if (!this._invTempHistory) this._invTempHistory = [];
      this._invTempHistory.push({ t: Date.now(), v: invTemp });
      // Keep last 10 readings
      if (this._invTempHistory.length > 10) this._invTempHistory.shift();
      // Check trend (last 5 readings rising)
      if (this._invTempHistory.length >= 5) {
        const last5 = this._invTempHistory.slice(-5);
        const rising = last5.every((p, i) => i === 0 || p.v >= last5[i - 1].v - 0.5);
        if (rising && invTemp > 58) {
          alerts.push({
            id: 'RISK_OVERHEAT', level: 'info', source: 'Falownik',
            title: `Ryzyko przegrzania: ${invTemp.toFixed(1)}°C ↑`,
            desc: 'Temperatura falownika stale rośnie — monitoruj',
            diag: {
              detected: `Temp. ${invTemp.toFixed(1)}°C, trend rosnący w ostatnich 5 odczytach`,
              reason: 'Temperatura falownika rośnie ciągle — może przekroczyć próg',
              causes: ['Duże obciążenie w ciepły dzień', 'Słaba wentylacja', 'Ekspozycja na słońce'],
              action: 'Zapewnij lepszą wentylację, ogranicz eksport jeśli to możliwe',
              severity: 'Predykcja ryzyka'
            }
          });
        }
      }
    }

    // Risk: grid curtailment
    const voltages = [this._nm('voltage_l1'), this._nm('voltage_l2'), this._nm('voltage_l3')].filter(v => v !== null);
    const gridPower = this._nm('grid_power') || 0;
    if (voltages.some(v => v > 248) && gridPower < -500) {
      alerts.push({
        id: 'RISK_CURTAILMENT', level: 'info', source: 'Sieć',
        title: 'Ryzyko odstawienia — wysokie napięcie + eksport',
        desc: 'Napięcie bliskie limitu przy aktywnym eksporcie',
        diag: {
          detected: `Napięcie > 248V, eksport: ${this._pw(Math.abs(gridPower))}`,
          reason: 'Falownik może zostać odstawiony przez ochronę napięciową',
          causes: ['Duży eksport PV podnosi napięcie', 'Słaba sieć lokalna', 'Wielu prosumentów w okolicy'],
          action: 'Włącz ograniczenie eksportu, ładuj baterię zamiast eksportować',
          severity: 'Predykcja ryzyka'
        }
      });
    }

    // Risk: low SOC before evening
    if (hour >= 14 && hour <= 17 && batSoc !== null && batSoc < 30) {
      const batPower = this._nm('battery_power') || 0;
      if (batPower <= 0) { // not charging
        alerts.push({
          id: 'RISK_LOW_SOC_EVENING', level: 'info', source: 'Bateria',
          title: `Niski SOC przed wieczorem: ${batSoc.toFixed(0)}%`,
          desc: 'Bateria może nie wystarczyć na wieczorny szczyt',
          diag: {
            detected: `SOC: ${batSoc.toFixed(0)}% o ${hour}:00, brak ładowania`,
            reason: 'SOC niski, a wieczorny szczyt taryfowy się zbliża',
            causes: ['Mała produkcja PV', 'Duże zużycie w ciągu dnia', 'Brak automatycznego ładowania'],
            action: 'Rozważ doładowanie z sieci lub PV przed szczytem',
            severity: 'Predykcja ryzyka'
          }
        });
      }
    }
  }

  _calcHealthScore(alerts) {
    const scores = { data: 100, inv: 100, pv: 100, bat: 100, grid: 100, alarms: 100 };

    // Data availability
    const coreSensors = ['pv_power', 'load_power', 'grid_power', 'battery_power', 'battery_soc'];
    let available = 0;
    coreSensors.forEach(k => { if (this._nm(k) !== null) available++; });
    scores.data = Math.round((available / coreSensors.length) * 100);

    // Inverter
    const invTemp = this._nm('inverter_temp');
    if (invTemp === null) { scores.inv = 50; }
    else if (invTemp > 75) { scores.inv = 10; }
    else if (invTemp > 65) { scores.inv = 60; }
    else if (invTemp > 55) { scores.inv = 80; }

    // PV — based on anomalies
    const pvAlerts = alerts.filter(a => a.source === 'PV');
    if (pvAlerts.some(a => a.level === 'critical')) scores.pv = 20;
    else if (pvAlerts.some(a => a.level === 'warning')) scores.pv = 60;
    else {
      // Night: PV is OK by default
      const hour = new Date().getHours();
      if (hour < 6 || hour > 21) scores.pv = 100;
    }

    // Battery
    const batSoc = this._nm('battery_soc');
    if (batSoc === null) { scores.bat = 30; }
    else {
      const batAlerts = alerts.filter(a => a.source === 'Bateria');
      if (batAlerts.some(a => a.level === 'critical')) scores.bat = 20;
      else if (batAlerts.some(a => a.level === 'warning')) scores.bat = 60;
    }

    // Grid
    const gridAlerts = alerts.filter(a => a.source === 'Sieć');
    if (gridAlerts.some(a => a.level === 'critical')) scores.grid = 20;
    else if (gridAlerts.some(a => a.level === 'warning')) scores.grid = 65;

    // Alarms penalty
    const critCount = alerts.filter(a => a.level === 'critical').length;
    const warnCount = alerts.filter(a => a.level === 'warning').length;
    scores.alarms = Math.max(0, 100 - critCount * 30 - warnCount * 10);

    // Weighted total
    const weights = { data: 0.15, inv: 0.20, pv: 0.20, bat: 0.15, grid: 0.15, alarms: 0.15 };
    let total = 0;
    for (const [k, w] of Object.entries(weights)) {
      total += scores[k] * w;
    }
    return { total: Math.round(total), ...scores };
  }

  _renderAlertStatusBar(alerts, health) {
    const bar = this.shadowRoot.getElementById('alert-status-bar');
    const icon = this.shadowRoot.getElementById('alert-status-icon');
    const text = this.shadowRoot.getElementById('alert-status-text');
    const count = this.shadowRoot.getElementById('alert-count');
    const lastUp = this.shadowRoot.getElementById('alert-last-update');
    const h24 = this.shadowRoot.getElementById('alert-24h-count');
    if (!bar) return;

    const critCount = alerts.filter(a => a.level === 'critical').length;
    const warnCount = alerts.filter(a => a.level === 'warning').length;
    const totalAlerts = alerts.length;

    // Determine global status
    let status = 'ok';
    let statusText = '✅ Instalacja OK';
    let statusIcon = '🟢';

    if (critCount > 0) {
      status = 'critical';
      statusText = `🔴 Awaria krytyczna (${critCount})`;
      statusIcon = '🔴';
    } else if (warnCount > 0) {
      status = 'warning';
      statusText = `🟡 Wykryto anomalię (${warnCount})`;
      statusIcon = '🟡';
    } else if (this._nm('pv_power') === null && this._nm('load_power') === null) {
      status = 'offline';
      statusText = '⚫ Brak danych / brak komunikacji';
      statusIcon = '⚫';
    }

    bar.className = `alert-status-bar ${status}`;
    icon.textContent = statusIcon;
    text.textContent = statusText;
    count.textContent = totalAlerts;
    lastUp.textContent = new Date().toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    // 24h count
    const now = Date.now();
    const h24Count = this._alertState.history.filter(h => (now - h.ts) < 86400000).length;
    h24.textContent = h24Count;

    // Health score ring
    const scoreEl = this.shadowRoot.getElementById('health-score-val');
    const ringFg = this.shadowRoot.getElementById('health-ring-fg');
    if (scoreEl) scoreEl.textContent = health.total;
    if (ringFg) {
      const circumference = 2 * Math.PI * 34; // r=34
      const offset = circumference * (1 - health.total / 100);
      ringFg.style.strokeDasharray = circumference;
      ringFg.style.strokeDashoffset = offset;
      // Color based on score
      if (health.total >= 80) ringFg.setAttribute('stroke', '#2ecc71');
      else if (health.total >= 50) ringFg.setAttribute('stroke', '#f59e0b');
      else ringFg.setAttribute('stroke', '#e74c3c');
    }
  }

  _renderAlertTiles() {
    // Falownik tile
    const invTemp = this._nm('inverter_temp');
    const pvPower = this._nm('pv_power');
    this._setText('at-inv-status', pvPower !== null ? 'Online' : 'Offline');
    this._setText('at-inv-temp', invTemp !== null ? `${invTemp.toFixed(1)}°C` : '—');
    this._setText('at-inv-contact', pvPower !== null ? `${new Date().toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit' })}` : 'Brak');

    let invStatus = 'ok';
    let invIssue = '';
    if (pvPower === null) { invStatus = 'offline'; invIssue = '⚫ Brak komunikacji'; }
    else if (invTemp !== null && invTemp > 75) { invStatus = 'critical'; invIssue = '🔴 Przegrzanie!'; }
    else if (invTemp !== null && invTemp > 65) { invStatus = 'warning'; invIssue = '🟡 Wysoka temperatura'; }
    else { invIssue = '✅ Brak błędów'; }
    this._setTileStatus('alert-tile-inv', invStatus, 'at-inv-issue', invIssue);

    // PV tile
    const pv1 = this._nm('pv1_power');
    const pv2 = this._nm('pv2_power');
    this._setText('at-pv-power', pvPower !== null ? this._pw(pvPower) : '—');
    // Expected from forecast
    const fcState = this._hass?.states?.['sensor.smartinghome_pv_forecast_power_now_total'];
    const expected = fcState ? parseFloat(fcState.state) : null;
    this._setText('at-pv-expected', expected ? this._pw(expected) : '—');
    // String delta (now shows absolute diff, informational only)
    if (pv1 !== null && pv2 !== null) {
      const delta = Math.abs(pv1 - pv2);
      this._setText('at-pv-delta', `${delta.toFixed(0)}W`);
    } else { this._setText('at-pv-delta', '—'); }

    let pvStatus = 'ok';
    let pvIssue = '✅ Produkcja w normie';
    const hour = new Date().getHours();
    if (hour < 6 || hour > 21) { pvIssue = '🌙 Noc — brak produkcji'; }
    else if (pvPower !== null && expected && expected > 500 && pvPower < expected * 0.65) {
      pvStatus = 'warning';
      pvIssue = `⚠️ -${Math.round((1 - pvPower / expected) * 100)}% poniżej normy`;
    }
    // Check for per-string baseline drops (from anomaly engine)
    const stringDropAlerts = (this._alertState?.alerts || []).filter(a => 
      a.id && a.id.startsWith('PV_STRING_DROP_')
    );
    if (stringDropAlerts.length > 0) {
      pvStatus = pvStatus === 'ok' ? 'warning' : pvStatus;
      const names = stringDropAlerts.map(a => a.id.replace('PV_STRING_DROP_', '')).join(', ');
      pvIssue += ` | ${names} poniżej normy`;
    }
    this._setTileStatus('alert-tile-pv', pvStatus, 'at-pv-issue', pvIssue);

    // Battery tile
    const batSoc = this._nm('battery_soc');
    const batPower = this._nm('battery_power');
    const batTemp = this._nm('battery_temp');
    this._setText('at-bat-soc', batSoc !== null ? `${batSoc.toFixed(0)}%` : '—');
    this._setText('at-bat-power', batPower !== null ? this._pw(Math.abs(batPower)) + (batPower > 50 ? ' ⬆️' : batPower < -50 ? ' ⬇️' : '') : '—');
    this._setText('at-bat-temp', batTemp !== null ? `${batTemp.toFixed(1)}°C` : '—');

    let batStatus = 'ok';
    let batIssue = '✅ Bateria OK';
    if (batSoc === null) { batStatus = 'critical'; batIssue = '🔴 BMS offline'; }
    else if (batSoc < 10) { batStatus = 'critical'; batIssue = '🔴 SOC krytycznie niski'; }
    else if (batSoc < 20) { batStatus = 'warning'; batIssue = '🟡 SOC niski'; }
    this._setTileStatus('alert-tile-bat', batStatus, 'at-bat-issue', batIssue);

    // Grid tile
    const vl1 = this._nm('voltage_l1');
    const vl2 = this._nm('voltage_l2');
    const vl3 = this._nm('voltage_l3');
    this._setText('at-grid-l1', vl1 !== null ? `${vl1.toFixed(1)}V` : '—');
    this._setText('at-grid-l2', vl2 !== null ? `${vl2.toFixed(1)}V` : '—');
    this._setText('at-grid-l3', vl3 !== null ? `${vl3.toFixed(1)}V` : '—');

    let gridStatus = 'ok';
    let gridIssue = '✅ Napięcia w normie';
    const allV = [vl1, vl2, vl3].filter(v => v !== null);
    if (allV.some(v => v > 253)) { gridStatus = 'critical'; gridIssue = '🔴 Napięcie krytyczne!'; }
    else if (allV.some(v => v > 245)) { gridStatus = 'warning'; gridIssue = '🟡 Napięcie wysokie'; }
    this._setTileStatus('alert-tile-grid', gridStatus, 'at-grid-issue', gridIssue);

    // Meter/CT tile
    const gridP = this._nm('grid_power') || 0;
    const importW = Math.max(gridP, 0);
    const exportW = Math.max(-gridP, 0);
    this._setText('at-meter-import', this._pw(importW));
    this._setText('at-meter-export', this._pw(exportW));

    let meterStatus = 'ok';
    let meterIssue = '✅ Pomiar OK';
    const pvP = this._nm('pv_power') || 0;
    if (pvP < 100 && exportW > 200) {
      meterStatus = 'warning'; meterIssue = '⚠️ Możliwy błąd CT';
    }
    this._setText('at-meter-status', meterStatus === 'ok' ? 'OK' : '⚠️');
    this._setTileStatus('alert-tile-meter', meterStatus, 'at-meter-issue', meterIssue);

    // Communication tile
    const haOnline = this._hass?.connected !== false;
    this._setText('at-comm-ha', haOnline ? 'OK' : '⚠️ Offline');
    this._setText('at-comm-modbus', pvPower !== null ? 'OK' : '—');
    this._setText('at-comm-update', new Date().toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit', second: '2-digit' }));

    let commStatus = haOnline && pvPower !== null ? 'ok' : (haOnline ? 'warning' : 'critical');
    let commIssue = commStatus === 'ok' ? '✅ Połączenie stabilne' : (commStatus === 'warning' ? '🟡 Sprawdź Modbus' : '🔴 Brak połączenia HA');
    this._setTileStatus('alert-tile-comm', commStatus, 'at-comm-issue', commIssue);
  }

  _setTileStatus(tileId, status, issueId, issueText) {
    const tile = this.shadowRoot.getElementById(tileId);
    const issue = this.shadowRoot.getElementById(issueId);
    if (tile) {
      tile.classList.remove('ok', 'warning', 'critical', 'offline');
      tile.classList.add(status);
    }
    if (issue) {
      issue.textContent = issueText;
      issue.style.display = issueText ? '' : 'none';
    }
  }

  _renderActiveAlerts(alerts) {
    const container = this.shadowRoot.getElementById('alert-list-items');
    const countEl = this.shadowRoot.getElementById('alert-active-count');
    if (!container) return;

    if (alerts.length === 0) {
      container.innerHTML = `<div class="alert-empty"><div class="alert-empty-icon">✅</div><div>Brak aktywnych alertów — instalacja działa prawidłowo</div></div>`;
      if (countEl) countEl.textContent = '0 alertów';
      return;
    }

    if (countEl) countEl.textContent = `${alerts.length} ${alerts.length === 1 ? 'alert' : 'alertów'}`;

    const levelIcons = { critical: '🔴', warning: '🟡', info: '🔵' };
    const levelCss = { critical: 'crit', warning: 'warn', info: 'info' };

    container.innerHTML = alerts.map((a, i) => `
      <div class="alert-item" onclick="this.getRootNode().host._showAlertDiagnosis(${i})">
        <div class="alert-level-badge ${levelCss[a.level] || 'info'}">${levelIcons[a.level] || 'ℹ️'}</div>
        <div class="alert-item-body">
          <div class="alert-item-title">${a.title}</div>
          <div class="alert-item-desc">${a.desc}</div>
        </div>
        <div class="alert-item-time">
          <div>${new Date().toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit' })}</div>
          <div class="alert-item-source">${a.source}</div>
        </div>
      </div>
    `).join('');
  }

  _renderHealthBreakdown(health) {
    const factors = [
      { id: 'data', val: health.data },
      { id: 'inv', val: health.inv },
      { id: 'pv', val: health.pv },
      { id: 'bat', val: health.bat },
      { id: 'grid', val: health.grid },
      { id: 'alarms', val: health.alarms },
    ];

    factors.forEach(f => {
      const valEl = this.shadowRoot.getElementById(`hf-${f.id}`);
      const barEl = this.shadowRoot.getElementById(`hf-${f.id}-bar`);
      if (valEl) valEl.textContent = `${f.val}/100`;
      if (barEl) {
        barEl.style.width = `${f.val}%`;
        if (f.val >= 80) barEl.style.background = '#2ecc71';
        else if (f.val >= 50) barEl.style.background = '#f59e0b';
        else barEl.style.background = '#e74c3c';
      }
    });
  }

  _renderAlertHistory() {
    const container = this.shadowRoot.getElementById('alert-history-items');
    if (!container) return;

    const now = Date.now();
    const recent = this._alertState.history
      .filter(h => (now - h.ts) < 86400000)
      .sort((a, b) => b.ts - a.ts)
      .slice(0, 20);

    if (recent.length === 0) {
      container.innerHTML = '<div style="color:#64748b; text-align:center; padding:16px; font-size:12px">Brak zarejestrowanych zdarzeń</div>';
      return;
    }

    const levelIcons = { critical: '🔴', warning: '🟡', info: '🔵' };

    container.innerHTML = recent.map(h => {
      const time = new Date(h.ts).toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      const ago = Math.round((now - h.ts) / 60000);
      const agoText = ago < 1 ? 'teraz' : ago < 60 ? `${ago} min temu` : `${Math.round(ago / 60)}h temu`;
      return `
        <div class="alert-item" style="opacity:0.7; cursor:default">
          <div class="alert-level-badge ${h.level === 'critical' ? 'crit' : h.level === 'warning' ? 'warn' : 'info'}">${levelIcons[h.level] || 'ℹ️'}</div>
          <div class="alert-item-body">
            <div class="alert-item-title" style="font-size:11px">${h.title}</div>
            <div class="alert-item-desc">${h.source}</div>
          </div>
          <div class="alert-item-time">
            <div>${time}</div>
            <div class="alert-item-source">${agoText}</div>
          </div>
        </div>
      `;
    }).join('');
  }

  // ── PV String Baseline Tracking ──

  /**
   * Track each string's performance over time and detect anomalies
   * by comparing to its own historical baseline at the same hour.
   *
   * Data structure (persisted to settings.json as pv_string_baselines):
   *   { PV1: { "8": [vals...], "9": [vals...], ... }, PV2: { ... } }
   *   Each hour key has an array of up to 7 samples (last 7 days).
   *
   * Algorithm:
   *   1. Record current power for this string at current hour
   *   2. Compare to average of previous samples at the same hour
   *   3. Alert if current output is >35% below baseline AND baseline > 200W
   */
  _trackAndCheckStringBaseline(stringName, currentPower, alerts) {
    if (currentPower === null || currentPower === undefined) return;

    const hour = new Date().getHours().toString();

    // Initialize baseline storage
    if (!this._pvStringBaselines) {
      this._pvStringBaselines = this._settings?.pv_string_baselines || {};
    }
    if (!this._pvStringBaselines[stringName]) {
      this._pvStringBaselines[stringName] = {};
    }
    const stringData = this._pvStringBaselines[stringName];
    if (!stringData[hour]) {
      stringData[hour] = [];
    }

    // Rate-limit recording — max once per 10 minutes per string
    const recordKey = `_pvBaselineLastRecord_${stringName}`;
    const now = Date.now();
    if (!this[recordKey] || (now - this[recordKey]) > 600000) {
      this[recordKey] = now;

      // Only record meaningful values (> 50W) to avoid cloudy/night noise
      if (currentPower > 50) {
        stringData[hour].push({
          w: Math.round(currentPower),
          ts: now,
        });

        // Keep only last 7 samples per hour slot (≈ 7 days of data)
        if (stringData[hour].length > 7) {
          stringData[hour] = stringData[hour].slice(-7);
        }

        // Persist baselines every 5 minutes  
        if (!this._lastBaselineSave || (now - this._lastBaselineSave) > 300000) {
          this._lastBaselineSave = now;
          this._savePanelSettings({ pv_string_baselines: this._pvStringBaselines });
        }
      }
    }

    // Need at least 3 historical samples for this hour to detect anomalies
    const samples = stringData[hour];
    if (!samples || samples.length < 3) return;

    // Calculate baseline: average of previous samples (exclude the one we just added)
    // Use samples older than 30 min to avoid self-comparison
    const cutoff = now - 1800000; // 30 min ago
    const historicalSamples = samples.filter(s => s.ts < cutoff);
    if (historicalSamples.length < 2) return;

    const baseline = historicalSamples.reduce((sum, s) => sum + s.w, 0) / historicalSamples.length;

    // Don't alert if baseline is very low (cloudy/early/late hours)
    if (baseline < 200) return;

    // Don't alert if current is very low but still reporting (could be sudden cloud)
    // Only alert if sustained drop: current < 65% of baseline
    if (currentPower < baseline * 0.65 && currentPower > 10) {
      const dropPct = Math.round((1 - currentPower / baseline) * 100);
      alerts.push({
        id: `PV_STRING_DROP_${stringName}`, level: 'warning', source: 'PV',
        title: `${stringName} spadek: -${dropPct}% vs norma`,
        desc: `Teraz: ${this._pw(currentPower)}, norma (${hour}:00): ${this._pw(baseline)}`,
        diag: {
          detected: `${stringName} produkuje ${dropPct}% mniej niż zwykle o tej porze (baseline: ${Math.round(baseline)}W z ${historicalSamples.length} dni)`,
          reason: `Porównanie z historyczną średnią tego samego stringa o godzinie ${hour}:00`,
          causes: [
            'Nowe zacienienie (drzewo, budynek, komin)',
            'Zabrudzenie paneli na tym stringu',
            'Uszkodzony panel lub optymizer',
            'Problem z MPPT falownika',
            'Degradacja paneli',
            'Krótkotrwałe zachmurzenie (poczekaj 15 min)',
          ],
          action: `Sprawdź ${stringName} wizualnie. Jeśli alert powtarza się codziennie o tej porze — prawdopodobnie nowe zacienienie.`,
          severity: dropPct > 50 ? 'Krytyczne — duża utrata produkcji' : 'Ważne — spadek wydajności'
        }
      });
    }
  }

  // ── Notification System Methods ──

  _sendAlertNotification(alert) {
    // Check config
    const cfg = this._settings?.notification_config;
    if (!cfg?.enabled) return;

    // Cooldown check (client-side)
    const cooldownMin = cfg.cooldown || 15;
    if (!this._notifLastSent) this._notifLastSent = {};
    const lastSent = this._notifLastSent[alert.id] || 0;
    if (Date.now() - lastSent < cooldownMin * 60000) return;

    // Level filter
    const levels = cfg.levels || ['critical', 'warning'];
    if (!levels.includes(alert.level)) return;

    // Dispatch to backend
    try {
      this._hass.callService('smartinghome', 'send_alert_notification', {
        alert_id: alert.id,
        level: alert.level,
        source: alert.source || 'System',
        title: alert.title || alert.id,
        message: alert.desc || alert.title || '',
        diag_action: alert.diag?.action || '',
      });
      this._notifLastSent[alert.id] = Date.now();
    } catch (e) {
      console.warn('Notification dispatch error:', e);
    }
  }

  // ── Multi-device Push Helpers ──

  _buildPushDeviceOptions() {
    const notifyServices = Object.keys(this._hass?.services?.notify || {});
    const mobileApps = notifyServices.filter(s => s.startsWith('mobile_app_'));
    const list = mobileApps.length > 0 ? mobileApps : notifyServices;
    return list.map(svc => {
      const entity = `notify.${svc}`;
      const isMobile = svc.startsWith('mobile_app_');
      const label = isMobile ? svc.replace('mobile_app_', '').replace(/_/g, ' ') : svc;
      const displayLabel = label.charAt(0).toUpperCase() + label.slice(1);
      return { value: entity, label: isMobile ? `📱 ${displayLabel}` : displayLabel };
    });
  }

  _renderPushDeviceRow(container, selectedValue, idx) {
    const options = this._pushDeviceOptions || [];
    const row = document.createElement('div');
    row.className = 'push-device-row';
    row.style.cssText = 'display:flex; align-items:center; gap:6px; margin-bottom:5px;';
    row.dataset.pushIdx = idx;

    const select = document.createElement('select');
    select.className = 'notif-push-device-select';
    select.style.cssText = 'flex:1; padding:6px 10px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); border-radius:6px; color:#e2e8f0; font-size:11px; outline:none; cursor:pointer;';
    select.innerHTML = '<option value="">— Wybierz urządzenie —</option>';
    options.forEach(opt => {
      const selected = opt.value === selectedValue ? ' selected' : '';
      select.innerHTML += `<option value="${opt.value}"${selected}>${opt.label}</option>`;
    });
    row.appendChild(select);

    // Show remove button only if more than 1 row
    const removeBtn = document.createElement('button');
    removeBtn.textContent = '✕';
    removeBtn.title = 'Usuń urządzenie';
    removeBtn.style.cssText = 'width:28px; height:28px; flex-shrink:0; background:rgba(231,76,60,0.12); border:1px solid rgba(231,76,60,0.3); border-radius:6px; color:#e74c3c; font-size:12px; cursor:pointer; transition:background 0.2s; display:flex; align-items:center; justify-content:center;';
    removeBtn.onmouseover = () => removeBtn.style.background = 'rgba(231,76,60,0.25)';
    removeBtn.onmouseout = () => removeBtn.style.background = 'rgba(231,76,60,0.12)';
    removeBtn.onclick = () => this._removePushDevice(idx);
    row.appendChild(removeBtn);

    container.appendChild(row);
  }

  _addPushDevice() {
    const container = this.shadowRoot.getElementById('notif-push-devices-list');
    if (!container) return;
    const currentCount = container.querySelectorAll('.push-device-row').length;
    if (currentCount >= 5) {
      // Max 5 devices
      return;
    }
    this._renderPushDeviceRow(container, '', currentCount);
    this._updateRemoveButtons();
  }

  _removePushDevice(idx) {
    const container = this.shadowRoot.getElementById('notif-push-devices-list');
    if (!container) return;
    const rows = container.querySelectorAll('.push-device-row');
    if (rows.length <= 1) return;  // Don't remove last one
    const row = container.querySelector(`.push-device-row[data-push-idx="${idx}"]`);
    if (row) row.remove();
    // Re-index remaining rows
    container.querySelectorAll('.push-device-row').forEach((r, i) => r.dataset.pushIdx = i);
    this._updateRemoveButtons();
  }

  _updateRemoveButtons() {
    const container = this.shadowRoot.getElementById('notif-push-devices-list');
    if (!container) return;
    const rows = container.querySelectorAll('.push-device-row');
    rows.forEach(row => {
      const btn = row.querySelector('button');
      if (btn) btn.style.visibility = rows.length > 1 ? 'visible' : 'hidden';
    });
  }

  _collectPushDevices() {
    const container = this.shadowRoot.getElementById('notif-push-devices-list');
    if (!container) return [];
    const selects = container.querySelectorAll('.notif-push-device-select');
    const entities = [];
    selects.forEach(sel => {
      const v = (sel.value || '').trim();
      if (v) entities.push(v);
    });
    return entities;
  }

  _onNotifToggle() {
    const master = this.shadowRoot.getElementById('notif-master')?.checked;
    const wrap = this.shadowRoot.getElementById('notif-channels-wrap');
    if (wrap) wrap.style.display = master ? 'block' : 'none';

    // Show/hide sub-fields
    const pushOn = this.shadowRoot.getElementById('notif-ch-push')?.checked;
    const smsOn = this.shadowRoot.getElementById('notif-ch-sms')?.checked;
    const emailOn = this.shadowRoot.getElementById('notif-ch-email')?.checked;
    const pushWrap = this.shadowRoot.getElementById('notif-push-entity-wrap');
    const smsWrap = this.shadowRoot.getElementById('notif-sms-wrap');
    const emailWrap = this.shadowRoot.getElementById('notif-email-wrap');
    if (pushWrap) pushWrap.style.display = pushOn ? 'block' : 'none';
    if (smsWrap) smsWrap.style.display = smsOn ? 'block' : 'none';
    if (emailWrap) emailWrap.style.display = emailOn ? 'block' : 'none';
  }

  _loadNotificationConfig() {
    const cfg = this._settings?.notification_config || {};
    const s = this.shadowRoot;
    if (!s) return;

    const el = id => s.getElementById(id);

    // Master
    const master = el('notif-master');
    if (master) master.checked = !!cfg.enabled;

    // Channels
    const ch = cfg.channels || {};
    if (el('notif-ch-push')) el('notif-ch-push').checked = !!ch.ha_push;
    if (el('notif-ch-persistent')) el('notif-ch-persistent').checked = !!ch.persistent;
    if (el('notif-ch-sms')) el('notif-ch-sms').checked = !!ch.sms;
    if (el('notif-ch-email')) el('notif-ch-email').checked = !!ch.email;

    // Inputs — populate push devices list (multi-device support)
    const devicesList = el('notif-push-devices-list');
    if (devicesList && this._hass?.services?.notify) {
      // Backward compat: old ha_push_entity (string) → new ha_push_entities (array)
      let savedEntities = cfg.ha_push_entities || [];
      if (savedEntities.length === 0 && cfg.ha_push_entity) {
        savedEntities = [cfg.ha_push_entity];
      }
      if (savedEntities.length === 0) savedEntities = [''];  // At least one empty row

      this._pushDeviceOptions = this._buildPushDeviceOptions();
      devicesList.innerHTML = '';
      savedEntities.forEach((entity, idx) => {
        this._renderPushDeviceRow(devicesList, entity, idx);
      });
      this._updateRemoveButtons();
    }
    if (el('notif-phone')) el('notif-phone').value = cfg.phone || '';
    if (el('notif-email')) el('notif-email').value = cfg.email || '';

    // Levels
    const levels = cfg.levels || ['critical', 'warning'];
    if (el('notif-lvl-critical')) el('notif-lvl-critical').checked = levels.includes('critical');
    if (el('notif-lvl-warning')) el('notif-lvl-warning').checked = levels.includes('warning');
    if (el('notif-lvl-info')) el('notif-lvl-info').checked = levels.includes('info');

    // Settings
    if (el('notif-cooldown')) el('notif-cooldown').value = cfg.cooldown || 15;
    if (el('notif-quiet-start')) el('notif-quiet-start').value = cfg.quiet_start || '22:00';
    if (el('notif-quiet-end')) el('notif-quiet-end').value = cfg.quiet_end || '07:00';

    // Trigger visibility update
    this._onNotifToggle();
  }

  _saveNotificationConfig() {
    const s = this.shadowRoot;
    const el = id => s.getElementById(id);

    // Collect levels
    const levels = [];
    if (el('notif-lvl-critical')?.checked) levels.push('critical');
    if (el('notif-lvl-warning')?.checked) levels.push('warning');
    if (el('notif-lvl-info')?.checked) levels.push('info');

    const cfg = {
      enabled: !!el('notif-master')?.checked,
      channels: {
        ha_push: !!el('notif-ch-push')?.checked,
        persistent: !!el('notif-ch-persistent')?.checked,
        sms: !!el('notif-ch-sms')?.checked,
        email: !!el('notif-ch-email')?.checked,
      },
      ha_push_entity: this._collectPushDevices()[0] || '',  // backward compat
      ha_push_entities: this._collectPushDevices(),
      phone: (el('notif-phone')?.value || '').trim(),
      email: (el('notif-email')?.value || '').trim(),
      levels,
      cooldown: parseInt(el('notif-cooldown')?.value || '15', 10) || 15,
      quiet_start: el('notif-quiet-start')?.value || '22:00',
      quiet_end: el('notif-quiet-end')?.value || '07:00',
    };

    // Update in-memory settings
    if (!this._settings) this._settings = {};
    this._settings.notification_config = cfg;

    // Persist to settings.json
    this._savePanelSettings({ notification_config: cfg });

    // Show saved status
    const status = el('notif-save-status');
    if (status) {
      status.textContent = '✅ Konfiguracja zapisana!';
      status.style.display = 'block';
      setTimeout(() => { status.style.display = 'none'; }, 3000);
    }
  }

  _testNotification() {
    // Auto-save config first to ensure backend has current settings
    this._saveNotificationConfig();

    // Small delay to let settings.json write complete
    setTimeout(() => {
      try {
        this._hass.callService('smartinghome', 'send_alert_notification', {
          alert_id: 'TEST_NOTIFICATION',
          level: 'critical',
          source: 'System',
          title: 'Test powiadomie\u0144 Smarting HOME',
          message: '\u2705 Test powiadomie\u0144 Smarting HOME \u2014 je\u015bli to widzisz, konfiguracja dzia\u0142a poprawnie!',
          diag_action: '',
        });

        const status = this.shadowRoot.getElementById('notif-save-status');
        if (status) {
          status.textContent = '\ud83d\udd14 Testowe powiadomienie wys\u0142ane!';
          status.style.color = '#00d4ff';
          status.style.display = 'block';
          setTimeout(() => {
            status.style.display = 'none';
            status.style.color = '#2ecc71';
          }, 4000);
        }
      } catch (e) {
        console.error('Test notification error:', e);
      }
    }, 500);
  }

  _renderNotificationLog() {
    const container = this.shadowRoot.getElementById('notif-log-items');
    if (!container) return;

    const log = this._settings?.notification_log || [];
    if (log.length === 0) {
      container.innerHTML = '<div style="text-align:center; padding:8px">Brak wysłanych powiadomień</div>';
      return;
    }

    const levelIcons = { critical: '🔴', warning: '🟡', info: '🔵' };
    const channelIcons = { ha_push: '📱', persistent: '📌', sms: '📱', email: '📧' };

    container.innerHTML = log.slice().reverse().slice(0, 15).map(e => {
      const icon = levelIcons[e.level] || 'ℹ️';
      const chIcons = (e.channels || []).map(c => channelIcons[c] || '').join(' ');
      const time = e.ts ? e.ts.split('T')[1]?.substring(0, 5) || '' : '';
      return `<div style="display:flex; align-items:center; gap:6px; padding:4px 0; border-bottom:1px solid rgba(255,255,255,0.04)">
        <span>${icon}</span>
        <span style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap">${e.title || e.alert_id}</span>
        <span style="font-size:9px; color:#64748b">${chIcons}</span>
        <span style="font-size:9px; color:#64748b; min-width:35px; text-align:right">${time}</span>
      </div>`;
    }).join('');
  }

  _showAlertDiagnosis(alertIndex) {
    const alert = this._alertState?.alerts?.[alertIndex];
    if (!alert || !alert.diag) return;
    const d = alert.diag;
    const levelColors = { critical: '#e74c3c', warning: '#f59e0b', info: '#00d4ff' };
    const color = levelColors[alert.level] || '#94a3b8';

    const html = `
      <div class="sh-modal-title" style="border-bottom:2px solid ${color}; padding-bottom:8px">
        🔍 Diagnoza: ${alert.title}
      </div>
      <div style="margin-top:12px">
        <div style="margin-bottom:12px">
          <div style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px">📋 Wykryto</div>
          <div style="font-size:12px; color:#e2e8f0; padding:8px 12px; background:rgba(255,255,255,0.03); border-radius:8px">${d.detected}</div>
        </div>
        <div style="margin-bottom:12px">
          <div style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px">🧠 Na jakiej podstawie</div>
          <div style="font-size:12px; color:#e2e8f0; padding:8px 12px; background:rgba(255,255,255,0.03); border-radius:8px">${d.reason}</div>
        </div>
        <div style="margin-bottom:12px">
          <div style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px">❓ Możliwe przyczyny</div>
          <div style="padding:8px 12px; background:rgba(255,255,255,0.03); border-radius:8px">
            ${d.causes.map(c => `<div style="font-size:11px; color:#cbd5e1; padding:3px 0">• ${c}</div>`).join('')}
          </div>
        </div>
        <div style="margin-bottom:12px">
          <div style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px">🛠️ Co zrobić</div>
          <div style="font-size:12px; color:#2ecc71; padding:8px 12px; background:rgba(46,204,113,0.06); border:1px solid rgba(46,204,113,0.15); border-radius:8px">${d.action}</div>
        </div>
        <div>
          <div style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px">⚡ Poziom ważności</div>
          <div style="font-size:12px; color:${color}; font-weight:600; padding:8px 12px; background:rgba(255,255,255,0.03); border-radius:8px">${d.severity}</div>
        </div>
      </div>
      <div class="sh-modal-actions" style="margin-top:16px">
        <button class="sh-modal-btn" onclick="this.getRootNode().host._closeModal()">Zamknij</button>
      </div>
    `;
    this._showModal(html);
  }

  _showAlertTileDetail(source) {
    const sourceNames = {
      inv: 'Falownik', pv: 'PV / Stringi', bat: 'Bateria',
      grid: 'Sieć', meter: 'Pomiar / CT', comm: 'Komunikacja'
    };
    const sourceAlerts = (this._alertState?.alerts || []).filter(a => {
      const sourceMap = { inv: 'Falownik', pv: 'PV', bat: 'Bateria', grid: 'Sieć', meter: 'Meter/CT', comm: 'Komunikacja' };
      return a.source === sourceMap[source] || a.source.includes(sourceMap[source] || '');
    });

    let detailHtml = '';
    if (source === 'inv') {
      const temp = this._nm('inverter_temp');
      const power = this._nm('inverter_power');
      const mode = this._s(this._m('inverter_model')) || '—';
      detailHtml = `
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:12px">
          <div style="padding:10px; background:rgba(255,255,255,0.03); border-radius:8px">
            <div style="font-size:9px; color:#64748b">Temperatura</div>
            <div style="font-size:18px; font-weight:700; color:${temp > 65 ? '#f59e0b' : temp > 75 ? '#e74c3c' : '#2ecc71'}">${temp !== null ? temp.toFixed(1) + '°C' : '—'}</div>
          </div>
          <div style="padding:10px; background:rgba(255,255,255,0.03); border-radius:8px">
            <div style="font-size:9px; color:#64748b">Moc wyjściowa</div>
            <div style="font-size:18px; font-weight:700; color:#00d4ff">${power !== null ? this._pw(power) : '—'}</div>
          </div>
        </div>
      `;
    } else if (source === 'grid') {
      const vl1 = this._nm('voltage_l1');
      const vl2 = this._nm('voltage_l2');
      const vl3 = this._nm('voltage_l3');
      const freq = this._nm('grid_frequency');
      const vColor = (v) => v > 253 ? '#e74c3c' : v > 245 ? '#f59e0b' : '#2ecc71';
      detailHtml = `
        <div style="display:grid; grid-template-columns:1fr 1fr 1fr 1fr; gap:8px; margin-bottom:12px">
          ${[['L1', vl1], ['L2', vl2], ['L3', vl3]].map(([l, v]) => `
            <div style="padding:10px; background:rgba(255,255,255,0.03); border-radius:8px; text-align:center">
              <div style="font-size:9px; color:#64748b">${l}</div>
              <div style="font-size:16px; font-weight:700; color:${v !== null ? vColor(v) : '#64748b'}">${v !== null ? v.toFixed(1) + 'V' : '—'}</div>
            </div>
          `).join('')}
          <div style="padding:10px; background:rgba(255,255,255,0.03); border-radius:8px; text-align:center">
            <div style="font-size:9px; color:#64748b">Freq</div>
            <div style="font-size:16px; font-weight:700; color:#00d4ff">${freq !== null ? freq.toFixed(2) + 'Hz' : '—'}</div>
          </div>
        </div>
      `;
    }

    const alertsHtml = sourceAlerts.length > 0
      ? sourceAlerts.map((a, i) => {
          const globalIdx = this._alertState.alerts.indexOf(a);
          return `<div class="alert-item" onclick="this.getRootNode().host._closeModal(); setTimeout(() => this.getRootNode().host._showAlertDiagnosis(${globalIdx}), 200)">
            <div class="alert-level-badge ${a.level === 'critical' ? 'crit' : a.level === 'warning' ? 'warn' : 'info'}">${a.level === 'critical' ? '🔴' : a.level === 'warning' ? '🟡' : '🔵'}</div>
            <div class="alert-item-body"><div class="alert-item-title">${a.title}</div><div class="alert-item-desc">${a.desc}</div></div>
          </div>`;
        }).join('')
      : '<div style="text-align:center; color:#2ecc71; padding:12px; font-size:12px">✅ Brak aktywnych alertów</div>';

    const html = `
      <div class="sh-modal-title">📋 ${sourceNames[source] || source} — szczegóły</div>
      ${detailHtml}
      <div style="font-size:11px; color:#94a3b8; margin-bottom:8px; text-transform:uppercase; letter-spacing:0.5px">Aktywne alerty (${sourceAlerts.length})</div>
      ${alertsHtml}
      <div class="sh-modal-actions" style="margin-top:14px">
        <button class="sh-modal-btn" onclick="this.getRootNode().host._closeModal()">Zamknij</button>
      </div>
    `;
    this._showModal(html);
  }

  /* ── Update all ─────────────────────────── */
  _updateAll() { this._updateFlow(); this._updateStats(); this._updateHomeImage(); this._updateG13Timeline(); this._updateSunWidget(); this._renderWeatherForecast(); this._updateEcowittCard(); this._calcHEMSScore(); this._updateWindTab(); this._updateHEMSArbitrage(); this._updateHistoryTab(); this._updateAutopilotVisibility(); this._updateAlertsVisibility(); this._updateSubMeters(); this._updateSubMetersInCard(); this._updateOverviewBanner(); this._updateAlertsTab(); this._updateSystemHealth(); this._updateForecastCharts().catch(e => console.error('[SH] charts err:', e)); }


  /* ── Overview Autopilot banner (runs every 5s via _updateAll) ── */
  _updateOverviewBanner() {
    const banner = this.shadowRoot.getElementById('ov-autopilot-banner');
    if (!banner) return;
    // Throttle: max once per 10s
    const now = Date.now();
    if (this._lastBannerUpdate && now - this._lastBannerUpdate < 10000) return;
    this._lastBannerUpdate = now;
    fetch('/local/smartinghome/settings.json?t=' + now)
      .then(r => r.json())
      .then(s => {
        const saved = s.autopilot_active_strategy;
        if (saved) {
          banner.style.display = 'block';
          const labels = {
            max_self_consumption: '🟢 Max Autokonsumpcja',
            max_profit: '💰 Max Zysk',
            battery_protection: '🔋 Ochrona Baterii',
            zero_export: '⚡ Zero Export',
            weather_adaptive: '🌧️ Pogodowy',
            ai_full_autonomy: '🧠 AI Pełna Autonomia',
          };
          const stEl = this.shadowRoot.getElementById('ov-ap-strategy');
          if (stEl) stEl.textContent = labels[saved] || saved;
          const live = s.autopilot_live;
          if (live) {
            const zEl = this.shadowRoot.getElementById('ov-ap-zone');
            if (zEl) {
              const zm = { off_peak: '🌙 Off-peak', morning_peak: '☀️ Szczyt poranny', afternoon_peak: '⚡ Szczyt popołudniowy' };
              zEl.textContent = zm[live.g13_zone] || '';
            }
          }
        } else {
          banner.style.display = 'none';
        }
      })
      .catch(() => {});
  }

  /* ── System Health Card ─────────────────── */
  _updateSystemHealth() {
    const _findHealth = (suffixes) => {
      if (this._hass?.states) {
        for (const suf of suffixes) {
          for (const eid of Object.keys(this._hass.states)) {
            if (eid.startsWith('sensor.') && eid.endsWith(suf)) {
              return this._hass.states[eid]?.state;
            }
          }
        }
      }
      return null;
    };

    // Battery SOH
    const soh = _findHealth(['_battery_soh', 'smartinghome_battery_soh', 'battery_state_of_health']);
    const sohEl = this.shadowRoot.getElementById('sh-soh-val');
    const sohLabel = this.shadowRoot.getElementById('sh-soh-label');
    if (sohEl && soh !== null && soh !== 'unavailable' && soh !== 'unknown') {
      const v = parseFloat(soh);
      sohEl.textContent = isNaN(v) ? '—' : v.toFixed(0);
      if (!isNaN(v)) {
        sohEl.style.color = v >= 90 ? '#2ecc71' : v >= 70 ? '#f59e0b' : '#e74c3c';
        if (sohLabel) {
          sohLabel.textContent = v >= 90 ? '✅ Zdrowa' : v >= 70 ? '⚠️ Obserwuj' : '🔴 Krytyczna';
          sohLabel.style.color = v >= 90 ? '#2ecc71' : v >= 70 ? '#f59e0b' : '#e74c3c';
        }
      }
    }

    // Inverter Thermal
    const radTemp = _findHealth(['_inverter_temp_radiator', 'smartinghome_inverter_temp_radiator', 'radiator_temperature']);
    const airTemp = _findHealth(['_inverter_temp_air', 'smartinghome_inverter_temp_air', 'inverter_temperature_air']);
    const tempEl = this.shadowRoot.getElementById('sh-inv-temp');
    const thermalLabel = this.shadowRoot.getElementById('sh-inv-thermal');
    const dispTemp = radTemp || airTemp;
    if (tempEl && dispTemp !== null && dispTemp !== 'unavailable' && dispTemp !== 'unknown') {
      const t = parseFloat(dispTemp);
      tempEl.textContent = isNaN(t) ? '—' : t.toFixed(1);
      if (!isNaN(t)) {
        tempEl.style.color = t < 55 ? '#2ecc71' : t < 65 ? '#f59e0b' : '#e74c3c';
        if (thermalLabel) {
          thermalLabel.textContent = t < 55 ? '✅ Norma' : t < 65 ? '🌡️ Ciepło' : '🔥 Gorąco!';
          thermalLabel.style.color = t < 55 ? '#2ecc71' : t < 65 ? '#f59e0b' : '#e74c3c';
        }
      }
    }

    // Grid Power Factor
    const pf = _findHealth(['_grid_power_factor', 'smartinghome_grid_power_factor', 'meter_power_factor1']);
    const pfEl = this.shadowRoot.getElementById('sh-pf-val');
    const pfLabel = this.shadowRoot.getElementById('sh-pf-label');
    if (pfEl && pf !== null && pf !== 'unavailable' && pf !== 'unknown') {
      const v = parseFloat(pf);
      pfEl.textContent = isNaN(v) ? '—' : v.toFixed(2);
      if (!isNaN(v)) {
        const absV = Math.abs(v);
        pfEl.style.color = absV >= 0.95 ? '#2ecc71' : absV >= 0.85 ? '#f59e0b' : '#e74c3c';
        if (pfLabel) {
          pfLabel.textContent = absV >= 0.95 ? '✅ Doskonała' : absV >= 0.85 ? '⚠️ Słaba' : '🔴 Krytyczna';
          pfLabel.style.color = absV >= 0.95 ? '#2ecc71' : absV >= 0.85 ? '#f59e0b' : '#e74c3c';
        }
      }
    }

    // Diagnostics
    const hasErrors = _findHealth(['_has_active_errors', 'smartinghome_has_active_errors']);
    const diagEl = this.shadowRoot.getElementById('sh-diag-icon');
    const diagLabel = this.shadowRoot.getElementById('sh-diag-label');
    if (diagEl) {
      if (hasErrors === 'True' || hasErrors === 'true' || hasErrors === '1') {
        diagEl.textContent = '⚠️';
        diagEl.style.color = '#e74c3c';
        if (diagLabel) { diagLabel.textContent = 'Wykryto błędy'; diagLabel.style.color = '#e74c3c'; }
      } else if (hasErrors !== null && hasErrors !== 'unavailable' && hasErrors !== 'unknown') {
        diagEl.textContent = '✅';
        diagEl.style.color = '#2ecc71';
        if (diagLabel) { diagLabel.textContent = 'OK — brak błędów'; diagLabel.style.color = '#2ecc71'; }
      }
    }
  }

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

    // ── Day/Night energy from backend (server-side, 24/7) ──
    // Discovery: HA entity IDs depend on device name slugification — search by suffix
    {
      const _findSensor = (suffixes) => {
        for (const suf of suffixes) {
          // Try direct entity IDs
          const direct = this._n(`sensor.${suf}`);
          if (direct !== null) return direct;
        }
        // Fallback: search all states for matching suffix
        if (this._hass?.states) {
          for (const suf of suffixes) {
            for (const eid of Object.keys(this._hass.states)) {
              if (eid.startsWith("sensor.") && eid.endsWith(suf)) {
                const v = this._n(eid);
                if (v !== null) return v;
              }
            }
          }
        }
        return null;
      };
      const dayKwh = _findSensor(['smartinghome_load_day', 'smartinghome_load_day_kwh', '_load_day', '_load_day_kwh']) ?? 0;
      const nightKwh = _findSensor(['smartinghome_load_night', 'smartinghome_load_night_kwh', '_load_night', '_load_night_kwh']) ?? 0;
      const pvToHome = _findSensor(['smartinghome_pv_to_home_today', 'smartinghome_load_pv_to_home_kwh', '_pv_to_home_today', '_load_pv_to_home_kwh']) ?? 0;

      const loadTodayNum = typeof loadTodayVal === 'number' ? loadTodayVal : parseFloat(loadTodayVal);
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
    // Grid — prefer SmartingHOME midnight-corrected sensors over raw GoodWe daily
    const _gridDaily = (suffixes) => {
      // Try SmartingHOME corrected sensors first
      if (this._hass?.states) {
        for (const suf of suffixes) {
          for (const eid of Object.keys(this._hass.states)) {
            if (eid.startsWith("sensor.") && eid.endsWith(suf)) {
              const v = this._n(eid);
              if (v !== null) return v.toFixed(1);
            }
          }
        }
      }
      return null;
    };
    const gridImpCorrected = _gridDaily(['_grid_import_daily', 'smartinghome_grid_import_today', '_grid_import_today']);
    const gridExpCorrected = _gridDaily(['_grid_export_daily', 'smartinghome_grid_export_today', '_grid_export_today']);
    this._setText("v-grid-import", `${gridImpCorrected ?? this._fm("grid_import_today")} kWh`);
    this._setText("v-grid-export", `${gridExpCorrected ?? this._fm("grid_export_today")} kWh`);
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
    const configBrand = (this._settings.inverter_brand || "").toLowerCase();

    if (invModel.includes("deye") || userChoice.includes("deye") || configBrand === "deye") {
      imgName = "deye";
    } else if (invModel.includes("growatt") || userChoice.includes("growatt") || configBrand === "growatt") {
      imgName = "growatt";
    } else if (invModel.includes("sofar") || userChoice.includes("sofar") || invModel.includes("solarman") || configBrand === "sofar") {
      imgName = "sofar";
    } else if (invModel.includes("goodwe") || userChoice.includes("goodwe") || configBrand === "goodwe") {
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
        sofar: 'https://smartinghome.pl/wp-content/uploads/2026/03/sofar.png',
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
    
    // RCE Sell price — v2: compute fallback from rce_pse_cena if coordinator sensor unavailable
    let rceSell = this._n("sensor.smartinghome_rce_sell_price") ?? this._n("sensor.rce_sell_price");
    if (rceSell === null || rceSell === 0) {
      const rceMwhRaw = this._n("sensor.rce_pse_cena");
      if (rceMwhRaw !== null && rceMwhRaw > 0) rceSell = rceMwhRaw / 1000 * 1.23;
    }
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

    // RCE Now (big card, zł/kWh) — v2: cena_za_kwh removed, compute from MWh
    const rceNowMwh = this._n("sensor.rce_pse_cena");
    const rceNowKwh = (rceNowMwh !== null) ? rceNowMwh / 1000 : rceSell;
    const rceNowEl = this.shadowRoot.getElementById("v-rce-now");
    if (rceNowEl && rceNowKwh !== null) {
      rceNowEl.textContent = `${rceNowKwh.toFixed(4)} zł`;
      rceNowEl.style.color = rceNowKwh > 0.6 ? "#2ecc71" : rceNowKwh > 0.3 ? "#f7b731" : rceNowKwh < 0 ? "#a855f7" : "#e74c3c";
    }
    this._setText("v-rce-now-mwh", rceNowMwh !== null ? `${rceNowMwh.toFixed(1)} PLN/MWh` : "— PLN/MWh");

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

    // vs Średnia — computed locally: (current - avg) / avg * 100
    {
      const vsAvgEl = this.shadowRoot.getElementById("v-rce-vs-avg");
      let rceVsAvg = null;
      if (rceNowMwh !== null && rceAvg !== null && rceAvg !== 0) {
        // rceNowMwh is PLN/MWh, rceAvg is already in zł/kWh (prosumer). Convert rceNow to same unit
        const rceNowSell = rceNowMwh / 1000 * 1.23;
        rceVsAvg = ((rceNowSell - rceAvg) / rceAvg) * 100;
      }
      if (vsAvgEl && rceVsAvg !== null) {
        vsAvgEl.textContent = `${rceVsAvg > 0 ? '+' : ''}${rceVsAvg.toFixed(1)}%`;
        vsAvgEl.style.color = rceVsAvg > 10 ? "#2ecc71" : rceVsAvg < -10 ? "#e74c3c" : "#f7b731";
        this._setText("v-rce-vs-label", rceVsAvg > 0 ? "powyżej średniej" : "poniżej średniej");
      }
    }

    // Trend — enum values: "rising", "falling", "stable"
    const rceTrend = this._s("sensor.smartinghome_rce_price_trend") || this._s("sensor.rce_price_trend") || "stable";
    const trendEl = this.shadowRoot.getElementById("v-rce-trend2");
    if (trendEl) {
      if (rceTrend === "rising") { trendEl.textContent = "📈"; this._setText("v-rce-trend-label", "Rośnie — warto czekać"); }
      else if (rceTrend === "falling") { trendEl.textContent = "📉"; this._setText("v-rce-trend-label", "Spada"); }
      else { trendEl.textContent = "➖"; this._setText("v-rce-trend-label", "Stabilny"); }
    }

    // Time Windows — compute from 96-point prices attribute (v2)
    // Helper: find cheapest/most expensive consecutive hour block from price entries
    const _findWindows = (pricesAttr) => {
      if (!pricesAttr || !Array.isArray(pricesAttr) || pricesAttr.length === 0) return { cheap: "—", expensive: "—" };
      // Group by hour and find min/max average price
      const hourMap = {};
      for (const p of pricesAttr) {
        try {
          const dt = p.dtime || p.period || p.time || "";
          const hour = parseInt(String(dt).substring(11, 13), 10);
          if (isNaN(hour)) continue;
          if (!hourMap[hour]) hourMap[hour] = [];
          hourMap[hour].push(parseFloat(p.rce_pln || p.price || p.value) || 0);
        } catch(e) { continue; }
      }
      let minHour = -1, maxHour = -1, minAvg = Infinity, maxAvg = -Infinity;
      for (const [h, vals] of Object.entries(hourMap)) {
        const avg = vals.reduce((a,b) => a+b, 0) / vals.length;
        if (avg < minAvg) { minAvg = avg; minHour = parseInt(h); }
        if (avg > maxAvg) { maxAvg = avg; maxHour = parseInt(h); }
      }
      const fmt = (h) => `${String(h).padStart(2,'0')}:00 – ${String((h+1)%24).padStart(2,'0')}:00`;
      const fmtPrice = (v) => `${(v/1000*1.23).toFixed(2)} zł`;
      return {
        cheap: minHour >= 0 ? `${fmt(minHour)} (${fmtPrice(minAvg)})` : "—",
        expensive: maxHour >= 0 ? `${fmt(maxHour)} (${fmtPrice(maxAvg)})` : "—",
      };
    };
    // Today windows — try multiple attribute names for v2 compatibility
    {
      const rcePriceState = this.hass && this.hass.states ? this.hass.states["sensor.rce_pse_cena"] : null;
      const attrs = rcePriceState && rcePriceState.attributes ? rcePriceState.attributes : {};
      const todayPrices = attrs.prices || attrs.forecast || attrs.price_list || null;
      const todayWindows = _findWindows(todayPrices);
      this._setText("v-cheapest-window", todayWindows.cheap);
      this._setText("v-expensive-window", todayWindows.expensive);
    }
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
    // Tomorrow windows — compute from rce_pse_cena_jutro prices attribute
    {
      const rceTomorrowState = this.hass && this.hass.states ? this.hass.states["sensor.rce_pse_cena_jutro"] : null;
      const tomorrowPrices = rceTomorrowState && rceTomorrowState.attributes ? rceTomorrowState.attributes.prices : null;
      const tomorrowWindows = _findWindows(tomorrowPrices);
      this._setText("v-cheapest-tomorrow", tomorrowWindows.cheap);
      this._setText("v-expensive-tomorrow", tomorrowWindows.expensive);
    }

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
    
    const socForArb = this._nm("battery_soc") || 0;

    // SOC display
    this._setText("v-soc-tab", `${Math.round(socForArb)}%`);
    const socBarTab = this.shadowRoot.getElementById("soc-fill-tab");
    if (socBarTab) {
      socBarTab.style.width = `${Math.min(100, Math.max(0, socForArb))}%`;
      socBarTab.style.background = socForArb > 50 ? "#2ecc71" : socForArb > 20 ? "#f39c12" : "#e74c3c";
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

    // ── Arbitrage KPIs — dynamic, SOC-aware ──
    const batCapKwh = this._settings.battery_capacity_kwh || 10.2;
    const minSocPct = 5;
    const rtEfficiency = 0.92;
    const availableKwh = Math.max(0, (socForArb - minSocPct) / 100 * batCapKwh);

    // Determine prices based on tariff type
    const tariffType = this._settings.tariff || 'g13';
    let arbBuyPrice, arbSellPrice;
    if (tariffType === 'dynamic') {
      // Dynamic tariff: use ENTSO-E min/max
      const entsoeMin = this._n("sensor.entso_e_koszt_all_in_min_dzisiaj");
      const entsoeMax = this._n("sensor.entso_e_koszt_all_in_max_dzisiaj");
      arbBuyPrice = entsoeMin != null && entsoeMin > 0 ? entsoeMin : 0.20;
      arbSellPrice = entsoeMax != null && entsoeMax > 0 ? entsoeMax : 0.80;
    } else {
      // G13: fixed prices
      arbBuyPrice = 0.63;  // off-peak
      arbSellPrice = 1.50; // afternoon peak
    }

    const arbMargin = arbSellPrice - arbBuyPrice;
    const arbPotentialNow = availableKwh * arbMargin * rtEfficiency;
    const arbPotentialFull = batCapKwh * arbMargin * rtEfficiency;

    // Update KPI cards
    this._setText("v-arbitrage-tab", `${arbPotentialNow.toFixed(2)} zł`);
    this._setText("v-arb-spread", `${arbMargin.toFixed(2)} zł`);
    this._setText("v-arb-buy-price", `${arbBuyPrice.toFixed(2)} zł`);
    this._setText("v-arb-sell-price", `${arbSellPrice.toFixed(2)} zł`);

    // Glow card when potential > 1 PLN
    const arbCard = this.shadowRoot.getElementById("arbitrage-card");
    if (arbCard) {
      if (arbPotentialNow > 1) arbCard.classList.add("glow-card");
      else arbCard.classList.remove("glow-card");
    }

    // ── Arbitrage strategy text — tariff-aware ──
    const arbStrategyTitle = this.shadowRoot.getElementById("arb-strategy-title");
    const arbStrategyBody = this.shadowRoot.getElementById("arb-strategy-body");
    if (arbStrategyTitle && arbStrategyBody) {
      if (tariffType === 'dynamic') {
        arbStrategyTitle.textContent = "📋 Strategia Dynamiczna ENTSO-E + HEMS";
        const entsoeNow = this._n("sensor.entso_e_koszt_all_in_teraz");
        const entsoeAvg = this._n("sensor.entso_e_srednia_dzisiaj");
        const nowPrice = entsoeNow != null ? entsoeNow.toFixed(2) : '—';
        const avgPrice = entsoeAvg != null ? entsoeAvg.toFixed(2) : '—';
        arbStrategyBody.innerHTML = `
          <strong style="color:#2ecc71">⏰ Tanie godziny</strong> — Ładuj baterię (cena < średnia ${avgPrice} zł)<br>
          <strong style="color:#e74c3c">⏰ Drogie godziny</strong> — Rozładowuj / sprzedawaj (cena > średnia)<br>
          <strong style="color:#f7b731">📊 Teraz:</strong> ${nowPrice} zł/kWh · Średnia: ${avgPrice} zł/kWh<br>
          <strong style="color:#00d4ff">💎 Marża/cykl:</strong> ${arbMargin.toFixed(2)} zł/kWh × ${batCapKwh.toFixed(1)} kWh = ${arbPotentialFull.toFixed(2)} zł
        `;
      } else {
        arbStrategyTitle.textContent = `📋 Strategia ${tariffType.toUpperCase()} + RCE`;
        const now = new Date();
        const hour = now.getHours();
        const isWinter = [0,1,2,9,10,11].includes(now.getMonth());
        const peakStart = isWinter ? 16 : 19;
        const peakEnd = isWinter ? 21 : 22;
        arbStrategyBody.innerHTML = `
          <strong style="color:#2ecc71">⏰ 22:00–06:00</strong> — Ładuj baterię (off-peak, ${arbBuyPrice.toFixed(2)} zł/kWh)<br>
          <strong style="color:#e67e22">⏰ 07:00–13:00</strong> — Rozładowuj na dom (szczyt poranny 0.91 zł/kWh)<br>
          <strong style="color:#f7b731">⏰ 13:00–${peakStart}:00</strong> — PV ładuje baterię + eksport nadwyżki<br>
          <strong style="color:#e74c3c">⏰ ${peakStart}:00–${peakEnd}:00</strong> — MAX rozładowanie! (${arbSellPrice.toFixed(2)} zł/kWh)<br>
          <div style="margin-top:4px; font-size:9px; color:#a855f7">💎 Marża/cykl: ${arbMargin.toFixed(2)} zł/kWh × ${batCapKwh.toFixed(1)} kWh × η${(rtEfficiency*100).toFixed(0)}% = ${arbPotentialFull.toFixed(2)} zł</div>
        `;
      }
    }

    // ── Arbitrage profit today — from battery throughput ──
    const arbChargeToday = this._nm("battery_charge_today") || 0;
    const arbDischargeToday = this._nm("battery_discharge_today") || 0;
    const arbCyclesKwh = Math.min(arbChargeToday, arbDischargeToday);
    const arbProfitToday = arbCyclesKwh * arbMargin * rtEfficiency;

    // ── Arbitrage next action — context-aware ──
    const nowH = new Date().getHours();
    const isWeekend = [0, 6].includes(new Date().getDay());
    let arbNextAction = "✅ System w trybie auto";
    if (tariffType === 'dynamic') {
      const eNow = this._n("sensor.entso_e_koszt_all_in_teraz");
      const eAvg = this._n("sensor.entso_e_srednia_dzisiaj");
      if (eNow != null && eAvg != null) {
        if (eNow < eAvg * 0.7) arbNextAction = "🔋 Cena niska — ładuj baterię z sieci";
        else if (eNow > eAvg * 1.3) arbNextAction = "🔥 Cena wysoka — rozładowuj / sprzedawaj";
        else arbNextAction = "📊 Cena neutralna — autokonsumpcja";
      }
    } else {
      if (isWeekend) {
        arbNextAction = socForArb < 50 ? "🔋 Weekend off-peak — ładuj (0.63 zł)" : "✅ Weekend — autokonsumpcja";
      } else if (nowH >= 22 || nowH < 7) {
        arbNextAction = socForArb < 80 ? "🔋 Off-peak noc — ładuj tanio (0.63 zł)" : "✅ Off-peak — bat. naładowana";
      } else if (nowH >= 7 && nowH < 13) {
        arbNextAction = socForArb > 30 ? "💰 Szczyt poranny — bat. zasila dom (0.91 zł)" : "⚠️ SOC niski — oszczędzaj na popołudnie";
      } else if (nowH >= 13 && nowH < 16) {
        arbNextAction = "🔋 Off-peak — ładuj z PV + sieci (0.63 zł)";
      } else {
        arbNextAction = socForArb > 20 ? "🔥 Szczyt popołudniowy — MAX rozładowanie! (1.50 zł)" : "⚠️ Bat. wyczerpana — import w szczycie";
      }
    }

    // ── Premium/Free tier detection for arbitrage ──
    const arbCta = this.shadowRoot.getElementById("arb-premium-cta");
    const arbActive = this.shadowRoot.getElementById("arb-premium-active");
    if (arbCta && arbActive) {
      if (tier === "PRO" || tier === "ENTERPRISE") {
        arbCta.style.display = "none";
        arbActive.style.display = "block";

        // ── Populate PRO section with real data ──
        // Auto-status
        const arbAutoStatus = this.shadowRoot.getElementById("v-arb-auto-status");
        if (arbAutoStatus) {
          const apLive = this._settings.autopilot_live;
          if (apLive && apLive.enabled) {
            const stratLabels = {
              max_self_consumption: '🟢 Autokonsumpcja',
              max_profit: '💰 Max Zysk',
              battery_protection: '🔋 Ochrona Bat.',
              zero_export: '⚡ Zero Export',
              weather_adaptive: '🌧️ Pogodowy',
              ai_full_autonomy: '🧠 AI Autonomia',
            };
            const stratLabel = stratLabels[apLive.strategy] || apLive.strategy || '';
            arbAutoStatus.textContent = `Aktywny ✅ ${stratLabel}`;
            arbAutoStatus.style.color = "#2ecc71";
          } else {
            arbAutoStatus.textContent = "Gotowy (uruchom Autopilota)";
            arbAutoStatus.style.color = "#f7b731";
          }
        }

        // Next action
        this._setText("v-arb-next-action", arbNextAction);

        // Profit today
        const arbProfitEl = this.shadowRoot.getElementById("v-arb-profit-today");
        if (arbProfitEl) {
          arbProfitEl.textContent = `${arbProfitToday.toFixed(2)} zł`;
          arbProfitEl.style.color = arbProfitToday > 0 ? "#2ecc71" : "#94a3b8";
        }
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

    // ROW 1b: Day/Night breakdown in Energy tab (from backend)
    {
      const _findSensor2 = (suffixes) => {
        for (const suf of suffixes) {
          const direct = this._n(`sensor.${suf}`);
          if (direct !== null) return direct;
        }
        if (this._hass?.states) {
          for (const suf of suffixes) {
            for (const eid of Object.keys(this._hass.states)) {
              if (eid.startsWith("sensor.") && eid.endsWith(suf)) {
                const v = this._n(eid);
                if (v !== null) return v;
              }
            }
          }
        }
        return null;
      };
      const enDayKwh = _findSensor2(['smartinghome_load_day', 'smartinghome_load_day_kwh', '_load_day', '_load_day_kwh']) ?? 0;
      const enNightKwh = _findSensor2(['smartinghome_load_night', 'smartinghome_load_night_kwh', '_load_night', '_load_night_kwh']) ?? 0;

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
        @keyframes apDotPulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.4; transform: scale(0.7); }
        }
        @keyframes bannerGlow {
          0%   { border-color: rgba(46,204,113,0.25); box-shadow: 0 0 0 rgba(46,204,113,0); }
          100% { border-color: rgba(46,204,113,0.4); box-shadow: 0 0 12px rgba(46,204,113,0.08); }
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
          overflow: hidden; min-width: 0;
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
          overflow-wrap: break-word; word-break: break-word;
          min-width: 0; overflow: hidden;
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

        /* ── Forecast Charts ── */
        .sh-chart-wrap {
          position: relative;
          background: rgba(10, 18, 35, 0.6);
          border: 1px solid rgba(255,255,255,0.06);
          border-radius: 14px;
          padding: 16px 12px 10px;
          overflow: visible;
        }
        .sh-chart-wrap::before {
          content: '';
          position: absolute; top: 0; left: 0; right: 0; height: 1px;
          background: linear-gradient(90deg, transparent, rgba(0,212,255,0.3), transparent);
        }
        .sh-chart-header {
          display: flex; align-items: center; justify-content: space-between;
          margin-bottom: 10px; padding: 0 4px;
        }
        .sh-chart-title {
          font-size: 11px; font-weight: 700; color: #a0aec0;
          text-transform: uppercase; letter-spacing: 1px;
        }
        .sh-chart-legend {
          display: flex; gap: 14px; align-items: center;
        }
        .sh-chart-legend-item {
          display: flex; align-items: center; gap: 5px;
          font-size: 9px; color: #94a3b8;
        }
        .sh-chart-legend-dot {
          width: 8px; height: 3px; border-radius: 2px;
        }
        .sh-chart-legend-dot.dashed {
          background: repeating-linear-gradient(90deg, currentColor 0px, currentColor 3px, transparent 3px, transparent 6px);
          width: 14px;
        }
        .sh-chart-svg {
          width: 100%; height: 100%; display: block;
        }
        .sh-chart-tooltip {
          position: absolute; display: none;
          background: rgba(15, 23, 42, 0.95);
          border: 1px solid rgba(0,212,255,0.3);
          border-radius: 8px; padding: 8px 12px;
          font-size: 10px; color: #e0e6ed;
          pointer-events: none; z-index: 50;
          backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
          box-shadow: 0 4px 16px rgba(0,0,0,0.4);
          white-space: nowrap;
          transform: translateX(-50%);
        }
        .sh-chart-tooltip-row {
          display: flex; align-items: center; gap: 6px; margin: 2px 0;
        }
        .sh-chart-tooltip-dot {
          width: 6px; height: 6px; border-radius: 50%;
        }
        .sh-chart-now-label {
          font-size: 8px; font-weight: 800; fill: #00d4ff;
          text-transform: uppercase; letter-spacing: 1px;
        }
        @keyframes shChartFadeIn {
          from { opacity: 0; transform: translateY(8px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .sh-chart-wrap { animation: shChartFadeIn 0.5s ease-out; }
        @keyframes shNowPulse {
          0%, 100% { opacity: 0.8; }
          50% { opacity: 0.3; }
        }
        .sh-mini-chart-wrap {
          position: relative;
          background: rgba(10, 18, 35, 0.4);
          border: 1px solid rgba(255,255,255,0.06);
          border-radius: 10px;
          padding: 10px 8px 6px;
          overflow: visible;
          margin-top: 10px;
        }
        .sh-mini-chart-wrap::before {
          content: '';
          position: absolute; top: 0; left: 0; right: 0; height: 1px;
          background: linear-gradient(90deg, transparent, rgba(247,183,49,0.3), transparent);
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
          .recommendation { padding: 8px; font-size: 11px; line-height: 1.5; overflow-wrap: break-word; word-break: break-word; }
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
        /* Entity Picker */
        .ep-search-wrap { margin-bottom: 10px; }
        .ep-search {
          width: 100%; padding: 10px 14px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.1);
          background: rgba(255,255,255,0.04); color: #e2e8f0; font-size: 13px; outline: none;
          box-sizing: border-box;
        }
        .ep-search:focus { border-color: rgba(0,212,255,0.4); box-shadow: 0 0 0 2px rgba(0,212,255,0.1); }
        .ep-list { max-height: 340px; overflow-y: auto; margin-bottom: 10px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.06); }
        .ep-item {
          padding: 10px 14px; cursor: pointer; border-bottom: 1px solid rgba(255,255,255,0.04);
          transition: background 0.15s;
        }
        .ep-item:hover { background: rgba(0,212,255,0.08); }
        .ep-item.ep-current { background: rgba(0,212,255,0.12); border-left: 3px solid #00d4ff; }
        .ep-name { font-size: 12px; font-weight: 600; color: #e2e8f0; margin-bottom: 2px; }
        .ep-meta { display: flex; justify-content: space-between; font-size: 10px; color: #64748b; }
        .ep-eid { opacity: 0.7; }
        .ep-val { font-weight: 600; color: #94a3b8; }
        .entity-placeholder {
          display: inline-block; padding: 3px 10px; border-radius: 8px; font-size: 10px;
          background: linear-gradient(135deg, rgba(0,212,255,0.08), rgba(100,116,139,0.1));
          border: 1px dashed rgba(0,212,255,0.3); color: #00d4ff; cursor: pointer;
          transition: all 0.2s; white-space: nowrap;
        }
        .entity-placeholder:hover {
          background: linear-gradient(135deg, rgba(0,212,255,0.15), rgba(100,116,139,0.15));
          border-color: rgba(0,212,255,0.5); transform: scale(1.02);
        }
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
          overflow-wrap: break-word; word-break: break-word;
          min-width: 0;
        }
        .ap-ai-analysis h2 { font-size: 16px; color: #fff; margin: 12px 0 6px; }
        .ap-ai-analysis h3 { font-size: 14px; color: #a78bfa; margin: 8px 0 4px; }
        .ap-ai-analysis strong { color: #fff; }
        .ap-ai-analysis table { width: 100%; border-collapse: collapse; font-size: 12px; margin: 8px 0; display: block; overflow-x: auto; -webkit-overflow-scrolling: touch; }
        .ap-ai-analysis th, .ap-ai-analysis td {
          padding: 4px 8px; border: 1px solid rgba(255,255,255,0.08); text-align: left;
          white-space: nowrap;
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
          .ap-ai-analysis { padding: 10px; font-size: 11px; line-height: 1.5; }
          .ap-ai-analysis h2 { font-size: 14px; }
          .ap-ai-analysis h3 { font-size: 12px; }
          .ap-ai-analysis table { font-size: 10px; }
          .ap-ai-analysis th, .ap-ai-analysis td { padding: 3px 5px; font-size: 10px; }
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

        /* ═══════ FORECAST TAB STYLES ═══════ */
        .fc-hero { position: relative; overflow: hidden; }
        .fc-hero::before { content: ''; position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: linear-gradient(135deg, rgba(247,183,49,0.06) 0%, rgba(0,212,255,0.04) 100%); pointer-events: none; }
        .fc-kpi-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 14px; position: relative; }
        .fc-kpi { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 14px; padding: 16px; text-align: center; transition: all 0.3s; }
        .fc-kpi:hover { border-color: rgba(247,183,49,0.3); box-shadow: 0 0 20px rgba(247,183,49,0.1); }
        .fc-kpi-label { font-size: 9px; color: #64748b; text-transform: uppercase; letter-spacing: 0.8px; }
        .fc-kpi-value { font-size: 28px; font-weight: 900; margin-top: 6px; line-height: 1; }
        .fc-kpi-sub { font-size: 10px; color: #94a3b8; margin-top: 4px; }
        .fc-week-bars { display: flex; align-items: flex-end; justify-content: space-between; gap: 8px; height: 200px; padding: 10px 0; }
        .fc-week-col { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; gap: 4px; height: 100%; }
        .fc-week-bar { width: 100%; border-radius: 8px 8px 4px 4px; transition: height 0.6s ease, background 0.3s; min-height: 4px; cursor: pointer; }
        .fc-week-bar:hover { filter: brightness(1.2); }
        .fc-week-val { font-size: 11px; font-weight: 700; }
        .fc-week-day { font-size: 10px; color: #94a3b8; font-weight: 600; }
        .fc-week-date { font-size: 8px; color: #475569; }
        .fc-hourly-row { display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-bottom: 1px solid rgba(255,255,255,0.04); transition: background 0.2s; }
        .fc-hourly-row:hover { background: rgba(255,255,255,0.03); }
        .fc-hourly-hour { font-size: 13px; font-weight: 700; color: #fff; min-width: 42px; }
        .fc-hourly-bar-wrap { flex: 1; height: 10px; background: rgba(255,255,255,0.06); border-radius: 5px; overflow: hidden; }
        .fc-hourly-bar { height: 100%; border-radius: 5px; transition: width 0.5s ease; }
        .fc-hourly-kw { font-size: 12px; font-weight: 700; min-width: 55px; text-align: right; }
        .fc-hourly-temp { font-size: 11px; color: #94a3b8; min-width: 35px; text-align: right; }
        .fc-decision { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 14px; padding: 14px 16px; margin-bottom: 8px; display: flex; align-items: flex-start; gap: 12px; transition: all 0.3s; }
        .fc-decision:hover { border-color: rgba(46,204,113,0.3); transform: translateX(4px); }
        .fc-decision-icon { font-size: 24px; flex-shrink: 0; }
        .fc-decision-body { flex: 1; min-width: 0; }
        .fc-decision-title { font-size: 13px; font-weight: 700; color: #fff; }
        .fc-decision-desc { font-size: 11px; color: #94a3b8; margin-top: 2px; overflow-wrap: break-word; word-break: break-word; }
        .fc-decision-badge { padding: 2px 8px; border-radius: 20px; font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; flex-shrink: 0; align-self: center; }
        .fc-badge-zysk { background: rgba(46,204,113,0.15); color: #2ecc71; border: 1px solid rgba(46,204,113,0.3); }
        .fc-badge-auto { background: rgba(0,212,255,0.15); color: #00d4ff; border: 1px solid rgba(0,212,255,0.3); }
        .fc-badge-load { background: rgba(247,183,49,0.15); color: #f7b731; border: 1px solid rgba(247,183,49,0.3); }
        .fc-strategy-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
        .fc-strategy-btn { background: rgba(255,255,255,0.03); border: 2px solid rgba(255,255,255,0.08); border-radius: 14px; padding: 16px 12px; text-align: center; cursor: pointer; transition: all 0.3s; }
        .fc-strategy-btn:hover { border-color: rgba(255,255,255,0.2); }
        .fc-strategy-btn.active { border-color: rgba(0,212,255,0.6); background: rgba(0,212,255,0.08); box-shadow: 0 0 20px rgba(0,212,255,0.15); }
        .fc-strategy-icon { font-size: 28px; margin-bottom: 6px; }
        .fc-strategy-name { font-size: 12px; font-weight: 700; color: #fff; }
        .fc-strategy-desc { font-size: 10px; color: #64748b; margin-top: 4px; }
        .fc-integ-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; }
        .fc-integ-item { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; padding: 12px; text-align: center; }
        .fc-integ-icon { font-size: 22px; }
        .fc-integ-name { font-size: 10px; font-weight: 700; color: #fff; margin-top: 4px; }
        .fc-integ-status { font-size: 9px; margin-top: 2px; }
        .fc-curve-svg { width: 100%; overflow: visible; }
        .fc-now-line { stroke: #f7b731; stroke-width: 1.5; stroke-dasharray: 4,3; }
        .fc-calib-bar { display: flex; align-items: center; gap: 10px; padding: 8px 12px; background: rgba(255,255,255,0.03); border-radius: 8px; margin-top: 6px; }
        .fc-calib-label { font-size: 11px; color: #94a3b8; flex: 1; }
        .fc-calib-value { font-size: 13px; font-weight: 700; color: #2ecc71; }
        @media (max-width: 768px) {
          .fc-kpi-grid { grid-template-columns: 1fr; gap: 8px; }
          .fc-kpi-value { font-size: 22px; }
          .fc-strategy-grid { grid-template-columns: 1fr; }
          .fc-week-bars { height: 140px; }
          .fc-decision { flex-wrap: wrap; }
        }
        @media (max-width: 480px) {
          .fc-kpi-grid { grid-template-columns: 1fr; }
          .fc-hourly-row { padding: 4px 6px; gap: 6px; }
          .fc-hourly-hour { font-size: 11px; min-width: 36px; }
          .fc-integ-grid { grid-template-columns: repeat(2, 1fr); }
        }

        /* ═══════════════════════════════════════ */
        /* ═══  ALERTS & ANOMALIES TAB         ═══ */
        /* ═══════════════════════════════════════ */

        /* Global Status Bar */
        .alert-status-bar {
          display: flex; align-items: center; gap: 16px; padding: 18px 22px;
          border-radius: 16px; margin-bottom: 14px;
          position: relative; overflow: hidden;
          border: 1px solid rgba(255,255,255,0.06);
        }
        .alert-status-bar.ok { background: linear-gradient(135deg, rgba(46,204,113,0.10), rgba(46,204,113,0.03)); border-color: rgba(46,204,113,0.25); }
        .alert-status-bar.warning { background: linear-gradient(135deg, rgba(245,158,11,0.12), rgba(245,158,11,0.03)); border-color: rgba(245,158,11,0.3); }
        .alert-status-bar.critical { background: linear-gradient(135deg, rgba(231,76,60,0.14), rgba(231,76,60,0.04)); border-color: rgba(231,76,60,0.35); animation: alert-critical-pulse 2s ease-in-out infinite; }
        .alert-status-bar.offline { background: linear-gradient(135deg, rgba(100,116,139,0.12), rgba(100,116,139,0.03)); border-color: rgba(100,116,139,0.25); }
        @keyframes alert-critical-pulse {
          0%, 100% { box-shadow: 0 0 20px rgba(231,76,60,0.15); }
          50% { box-shadow: 0 0 40px rgba(231,76,60,0.35), inset 0 0 20px rgba(231,76,60,0.08); }
        }
        .alert-status-icon { font-size: 36px; flex-shrink: 0; }
        .alert-status-info { flex: 1; min-width: 0; }
        .alert-status-label { font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 1px; }
        .alert-status-text { font-size: 18px; font-weight: 800; color: #fff; margin-top: 2px; }
        .alert-status-meta { font-size: 11px; color: #94a3b8; margin-top: 4px; display: flex; gap: 16px; flex-wrap: wrap; }
        .alert-status-meta span { white-space: nowrap; }

        /* Health Score Ring */
        .health-score-wrap { display: flex; flex-direction: column; align-items: center; flex-shrink: 0; }
        .health-score-ring { position: relative; width: 80px; height: 80px; }
        .health-score-ring svg { transform: rotate(-90deg); }
        .health-score-ring .ring-bg { fill: none; stroke: rgba(255,255,255,0.06); stroke-width: 6; }
        .health-score-ring .ring-fg { fill: none; stroke-width: 6; stroke-linecap: round; transition: stroke-dashoffset 1s ease, stroke 0.5s; }
        .health-score-val { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); font-size: 22px; font-weight: 900; color: #fff; }
        .health-score-label { font-size: 9px; color: #64748b; text-transform: uppercase; letter-spacing: 0.8px; margin-top: 4px; }

        /* Source Tiles Grid */
        .alert-tiles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 14px; }
        .alert-tile {
          background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06);
          border-radius: 14px; padding: 14px 16px; position: relative; overflow: hidden;
          transition: all 0.3s; cursor: pointer;
        }
        .alert-tile:hover { background: rgba(255,255,255,0.04); transform: translateY(-1px); }
        .alert-tile::before {
          content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
          border-radius: 14px 14px 0 0; transition: background 0.3s;
        }
        .alert-tile.ok::before { background: linear-gradient(90deg, #2ecc71, #27ae60); }
        .alert-tile.warning::before { background: linear-gradient(90deg, #f59e0b, #f39c12); }
        .alert-tile.critical::before { background: linear-gradient(90deg, #e74c3c, #c0392b); }
        .alert-tile.offline::before { background: linear-gradient(90deg, #64748b, #475569); }
        .alert-tile-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
        .alert-tile-icon { font-size: 20px; }
        .alert-tile-name { font-size: 11px; font-weight: 700; color: #e2e8f0; text-transform: uppercase; letter-spacing: 0.5px; }
        .alert-tile-dot { width: 8px; height: 8px; border-radius: 50%; margin-left: auto; flex-shrink: 0; }
        .alert-tile.ok .alert-tile-dot { background: #2ecc71; box-shadow: 0 0 8px rgba(46,204,113,0.5); }
        .alert-tile.warning .alert-tile-dot { background: #f59e0b; box-shadow: 0 0 8px rgba(245,158,11,0.5); animation: dot-pulse-warn 1.5s ease-in-out infinite; }
        .alert-tile.critical .alert-tile-dot { background: #e74c3c; box-shadow: 0 0 8px rgba(231,76,60,0.5); animation: dot-pulse-crit 1s ease-in-out infinite; }
        .alert-tile.offline .alert-tile-dot { background: #64748b; }
        @keyframes dot-pulse-warn { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
        @keyframes dot-pulse-crit { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.5; transform: scale(1.3); } }
        .alert-tile-values { display: flex; flex-direction: column; gap: 4px; }
        .alert-tile-row { display: flex; justify-content: space-between; align-items: center; }
        .alert-tile-row .atl { font-size: 10px; color: #64748b; }
        .alert-tile-row .atv { font-size: 12px; font-weight: 600; color: #cbd5e1; }
        .alert-tile-issue { font-size: 10px; color: #f59e0b; margin-top: 6px; padding: 4px 8px; background: rgba(245,158,11,0.08); border-radius: 6px; font-weight: 500; }
        .alert-tile.critical .alert-tile-issue { color: #e74c3c; background: rgba(231,76,60,0.08); }
        .alert-tile.ok .alert-tile-issue { color: #2ecc71; background: rgba(46,204,113,0.08); }

        /* Active Alerts Table */
        .alert-list { margin-bottom: 14px; }
        .alert-list-header {
          display: flex; align-items: center; justify-content: space-between;
          padding: 10px 16px; margin-bottom: 8px;
        }
        .alert-list-title { font-size: 12px; font-weight: 700; color: #e2e8f0; text-transform: uppercase; letter-spacing: 0.8px; }
        .alert-list-count { font-size: 11px; color: #94a3b8; }
        .alert-item {
          display: flex; align-items: center; gap: 12px; padding: 10px 16px;
          background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.05);
          border-radius: 10px; margin-bottom: 6px; cursor: pointer; transition: all 0.2s;
        }
        .alert-item:hover { background: rgba(255,255,255,0.05); transform: translateX(2px); }
        .alert-level-badge {
          width: 28px; height: 28px; border-radius: 8px; display: flex;
          align-items: center; justify-content: center; font-size: 14px; flex-shrink: 0;
        }
        .alert-level-badge.crit { background: rgba(231,76,60,0.15); }
        .alert-level-badge.warn { background: rgba(245,158,11,0.15); }
        .alert-level-badge.info { background: rgba(0,212,255,0.12); }
        .alert-item-body { flex: 1; min-width: 0; }
        .alert-item-title { font-size: 12px; font-weight: 600; color: #e2e8f0; }
        .alert-item-desc { font-size: 10px; color: #94a3b8; margin-top: 2px; overflow-wrap: break-word; word-break: break-word; }
        .alert-item-time { font-size: 10px; color: #64748b; flex-shrink: 0; text-align: right; }
        .alert-item-source { font-size: 9px; color: #475569; }
        .alert-empty {
          text-align: center; padding: 30px 16px; color: #64748b;
          font-size: 13px;
        }
        .alert-empty-icon { font-size: 40px; margin-bottom: 8px; }

        /* Health Breakdown */
        .health-breakdown { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 12px; }
        .health-factor {
          display: flex; align-items: center; gap: 8px; padding: 8px 12px;
          background: rgba(255,255,255,0.02); border-radius: 8px;
        }
        .health-factor-icon { font-size: 16px; }
        .health-factor-info { flex: 1; }
        .health-factor-name { font-size: 10px; color: #64748b; }
        .health-factor-val { font-size: 13px; font-weight: 700; color: #cbd5e1; }
        .health-factor-bar { width: 100%; height: 3px; background: rgba(255,255,255,0.06); border-radius: 2px; margin-top: 3px; }
        .health-factor-bar-fill { height: 100%; border-radius: 2px; transition: width 0.5s, background 0.3s; }

        /* Alert acknowledge */
        .alert-ack-btn {
          padding: 6px 14px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.12);
          background: rgba(255,255,255,0.04); color: #94a3b8; font-size: 10px;
          cursor: pointer; transition: all 0.2s; font-weight: 600; white-space: nowrap;
        }
        .alert-ack-btn:hover { background: rgba(255,255,255,0.08); color: #fff; }

        /* Responsive: Alerts tab */
        @media (max-width: 768px) {
          .alert-tiles { grid-template-columns: repeat(2, 1fr); }
          .alert-status-bar { flex-wrap: wrap; gap: 10px; padding: 14px 16px; }
          .health-score-ring { width: 60px; height: 60px; }
          .health-score-val { font-size: 18px; }
          .health-breakdown { grid-template-columns: repeat(2, 1fr); }
        }
        @media (max-width: 480px) {
          .alert-tiles { grid-template-columns: 1fr; }
          .alert-status-bar { flex-direction: column; align-items: flex-start; }
          .health-score-wrap { align-self: center; }
          .alert-status-meta { flex-direction: column; gap: 4px; }
          .health-breakdown { grid-template-columns: 1fr; }
        }

        /* Toggle Switch */
        .toggle-switch {
          position: relative; display: inline-block; width: 42px; height: 24px; flex-shrink: 0;
        }
        .toggle-switch input { opacity: 0; width: 0; height: 0; }
        .toggle-slider {
          position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
          background: rgba(255,255,255,0.1); border-radius: 24px; transition: 0.3s;
        }
        .toggle-slider::before {
          content: ""; position: absolute; height: 18px; width: 18px; left: 3px; bottom: 3px;
          background: #94a3b8; border-radius: 50%; transition: 0.3s;
        }
        .toggle-switch input:checked + .toggle-slider { background: rgba(0,212,255,0.35); }
        .toggle-switch input:checked + .toggle-slider::before { transform: translateX(18px); background: #00d4ff; }
        .toggle-switch.sm { width: 34px; height: 20px; }
        .toggle-switch.sm .toggle-slider::before { height: 14px; width: 14px; }
        .toggle-switch.sm input:checked + .toggle-slider::before { transform: translateX(14px); }

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
              <button class="tab-btn" data-tab="forecast" onclick="this.getRootNode().host._switchTab('forecast')">☀️ Prognoza</button>
              <button class="tab-btn" data-tab="alerts" onclick="this.getRootNode().host._switchTab('alerts')" id="tab-btn-alerts" style="display:none">⚠️ Awarie</button>
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
          <!-- Autopilot Status Banner -->
          <div id="ov-autopilot-banner" style="display:none; margin:0 8px 6px; padding:8px 16px; border-radius:10px; background:linear-gradient(135deg, rgba(46,204,113,0.08) 0%, rgba(0,212,255,0.06) 100%); border:1px solid rgba(46,204,113,0.25); backdrop-filter:blur(8px); animation:bannerGlow 3s ease-in-out infinite alternate">
            <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap">
              <div style="display:flex; align-items:center; gap:10px">
                <div style="width:8px; height:8px; border-radius:50%; background:#2ecc71; box-shadow:0 0 8px #2ecc71; animation:apDotPulse 2s ease-in-out infinite"></div>
                <div style="font-size:11px; font-weight:700; color:#2ecc71; text-transform:uppercase; letter-spacing:1.2px">🧠 AUTOPILOT AKTYWNY</div>
              </div>
              <div style="display:flex; align-items:center; gap:8px">
                <div id="ov-ap-strategy" style="font-size:12px; font-weight:600; color:#f8fafc; background:rgba(255,255,255,0.06); padding:3px 10px; border-radius:6px">—</div>
                <div id="ov-ap-zone" style="font-size:10px; font-weight:600; color:#94a3b8">—</div>
              </div>
            </div>
          </div>
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

          <!-- 🩺 System Health -->
          <div class="card" style="margin-top:10px">
            <div class="card-title">🩺 Zdrowie systemu</div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; padding:4px 0" id="sh-health-grid">
              <!-- Battery SOH -->
              <div style="background:rgba(255,255,255,0.03); border-radius:10px; padding:12px; border:1px solid rgba(255,255,255,0.06)">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:6px">🔋 Bateria SOH</div>
                <div style="display:flex; align-items:baseline; gap:4px">
                  <div style="font-size:22px; font-weight:800" id="sh-soh-val">—</div>
                  <div style="font-size:11px; color:#94a3b8">%</div>
                </div>
                <div style="font-size:10px; margin-top:4px; font-weight:600" id="sh-soh-label">—</div>
              </div>
              <!-- Inverter Thermal -->
              <div style="background:rgba(255,255,255,0.03); border-radius:10px; padding:12px; border:1px solid rgba(255,255,255,0.06)">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:6px">🌡️ Falownik</div>
                <div style="display:flex; align-items:baseline; gap:4px">
                  <div style="font-size:22px; font-weight:800" id="sh-inv-temp">—</div>
                  <div style="font-size:11px; color:#94a3b8">°C</div>
                </div>
                <div style="font-size:10px; margin-top:4px; font-weight:600" id="sh-inv-thermal">—</div>
              </div>
              <!-- Grid Quality -->
              <div style="background:rgba(255,255,255,0.03); border-radius:10px; padding:12px; border:1px solid rgba(255,255,255,0.06)">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:6px">⚡ Jakość sieci</div>
                <div style="display:flex; align-items:baseline; gap:4px">
                  <div style="font-size:22px; font-weight:800" id="sh-pf-val">—</div>
                  <div style="font-size:11px; color:#94a3b8">PF</div>
                </div>
                <div style="font-size:10px; margin-top:4px; font-weight:600" id="sh-pf-label">—</div>
              </div>
              <!-- Diagnostics -->
              <div style="background:rgba(255,255,255,0.03); border-radius:10px; padding:12px; border:1px solid rgba(255,255,255,0.06)">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:6px">🔧 Diagnostyka</div>
                <div style="font-size:22px; font-weight:800" id="sh-diag-icon">—</div>
                <div style="font-size:10px; margin-top:4px; font-weight:600" id="sh-diag-label">—</div>
              </div>
            </div>
          </div>

          <!-- ±12h Mini Forecast Chart -->
          <div class="card" style="margin-top:10px">
            <div class="sh-chart-header" style="margin-bottom:4px">
              <div class="sh-chart-title" style="font-size:10px">📈 PV ±12h: Prognoza vs Rzeczywistość</div>
              <div class="sh-chart-legend">
                <div class="sh-chart-legend-item"><div class="sh-chart-legend-dot" style="background:#f7b731"></div>Rzecz.</div>
                <div class="sh-chart-legend-item"><div class="sh-chart-legend-dot dashed" style="color:#a855f7"></div>Progn.</div>
              </div>
            </div>
            <div class="sh-mini-chart-wrap" id="sh-chart-ov-mini" style="height:160px"><div style="display:flex;align-items:center;justify-content:center;height:100%;color:#475569;font-size:10px">⏳ Ładowanie...</div></div>
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

          <!-- ROW 5b: ±12h Forecast Charts -->
          <div class="card" style="margin-bottom:14px">
            <div class="sh-chart-header">
              <div class="sh-chart-title">📈 Produkcja PV — Prognoza vs Rzeczywistość (±12h)</div>
              <div class="sh-chart-legend">
                <div class="sh-chart-legend-item"><div class="sh-chart-legend-dot" style="background:#f7b731"></div>Rzeczywiste</div>
                <div class="sh-chart-legend-item"><div class="sh-chart-legend-dot dashed" style="color:#a855f7"></div>Prognoza</div>
              </div>
            </div>
            <div class="sh-chart-wrap" id="sh-chart-pv" style="height:300px"><div style="display:flex;align-items:center;justify-content:center;height:100%;color:#475569;font-size:11px">⏳ Ładowanie danych z Recorder...</div></div>
          </div>

          <div class="card" style="margin-bottom:14px">
            <div class="sh-chart-header">
              <div class="sh-chart-title">🏠 Zużycie domu — Profil vs Rzeczywistość (±12h)</div>
              <div class="sh-chart-legend">
                <div class="sh-chart-legend-item"><div class="sh-chart-legend-dot" style="background:#2ecc71"></div>Rzeczywiste</div>
                <div class="sh-chart-legend-item"><div class="sh-chart-legend-dot dashed" style="color:#00d4ff"></div>Profil (śr. 7 dni)</div>
              </div>
            </div>
            <div class="sh-chart-wrap" id="sh-chart-load" style="height:300px"><div style="display:flex;align-items:center;justify-content:center;height:100%;color:#475569;font-size:11px">⏳ Ładowanie danych z Recorder...</div></div>
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
            <div style="display:flex; align-items:center; gap:8px; min-width:0">
              <div style="font-size:20px; flex-shrink:0">⚡</div>
              <div style="min-width:0; overflow:hidden">
                <div style="font-size:10px; color:#f7b731; text-transform:uppercase; font-weight:700">Rekomendacja HEMS</div>
                <div style="font-size:14px; font-weight:600; color:#fff; margin-top:2px; overflow-wrap:break-word; word-break:break-word" id="v-hems-rec-tariff">—</div>
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

          <!-- ROW 7: Provider Pricing Table (dynamic) -->
          <div class="card" style="margin-bottom:14px">
            <div class="card-title" id="pricing-table-title">📋 Cennik Tauron 2026 — porównanie taryf</div>
            <div style="overflow-x:auto" id="pricing-table-body">
            </div>
            <div style="font-size:9px; color:#64748b; margin-top:6px; line-height:1.4" id="pricing-table-footer">
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

          <!-- ROW 3b: ±12h SOC & Battery Power Charts -->
          <div class="grid-cards gc-2" style="margin-bottom:12px">
            <div class="card">
              <div class="sh-chart-header">
                <div class="sh-chart-title">🔋 SOC Baterii (±12h)</div>
                <div class="sh-chart-legend">
                  <div class="sh-chart-legend-item"><div class="sh-chart-legend-dot" style="background:#00d4ff"></div>SOC</div>
                </div>
              </div>
              <div class="sh-chart-wrap" id="sh-chart-soc" style="height:260px"><div style="display:flex;align-items:center;justify-content:center;height:100%;color:#475569;font-size:11px">⏳ Ładowanie danych SOC...</div></div>
            </div>
            <div class="card">
              <div class="sh-chart-header">
                <div class="sh-chart-title">⚡ Moc baterii (±12h)</div>
                <div class="sh-chart-legend">
                  <div class="sh-chart-legend-item"><div class="sh-chart-legend-dot" style="background:#a855f7"></div>Ładowanie/Rozładowanie</div>
                </div>
              </div>
              <div class="sh-chart-wrap" id="sh-chart-batt-power" style="height:260px"><div style="display:flex;align-items:center;justify-content:center;height:100%;color:#475569;font-size:11px">⏳ Ładowanie danych baterii...</div></div>
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
              <button class="action-btn hems-force-btn" id="hems-btn-charge" style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);color:#94a3b8" onclick="this.getRootNode().host._executeForceAction('charge')">🔋 Wymuś Ładow.</button>
              <button class="action-btn hems-force-btn" id="hems-btn-discharge" style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);color:#94a3b8" onclick="this.getRootNode().host._executeForceAction('discharge')">⚡ Wymuś Rozład.</button>
              <button class="action-btn hems-force-btn" id="hems-btn-stop" style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);color:#94a3b8" onclick="this.getRootNode().host._executeForceAction('emergency_stop')">🚨 STOP</button>
            </div>
          </div>

          <!-- ═══ AI ADVISOR ═══ -->
          <div class="card" style="margin-bottom:14px">
            <div class="card-title">🤖 AI Energy Advisor</div>
            <div style="display:flex; gap:10px; align-items:flex-start; min-width:0">
              <div style="font-size:28px; filter:drop-shadow(0 0 5px rgba(0,212,255,0.5)); flex-shrink:0">🤖</div>
              <div class="recommendation" style="flex:1; min-width:0" id="v-hems-rec-hems">Ładowanie porady z asystenta...</div>
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
            <div style="font-size:10px; color:#94a3b8; margin-bottom:10px">Pełna symulacja pracy instalacji: produkcja PV, profil zużycia, praca magazynu, import/eksport i strategia automatyki — osobna per taryfa.</div>
            <div style="display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin-bottom:14px; padding:10px; background:rgba(255,255,255,0.03); border-radius:8px; border:1px solid rgba(255,255,255,0.06)">
              <span style="font-size:11px; color:#94a3b8; white-space:nowrap">💰 Koszt instalacji</span>
              <input id="roi-invest-input" type="number" value="" placeholder="np. 45000" style="width:100px; background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.15); border-radius:6px; color:#fff; padding:6px 10px; font-size:13px; text-align:right" onchange="this.getRootNode().host._saveRoiInvestment(this.value)">
              <span style="font-size:12px; color:#94a3b8">zł</span>
              <div style="flex:1"></div>
              <span style="font-size:10px; color:#64748b" id="roi-period-label">Baza: —</span>
            </div>
            <div style="display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-bottom:14px; padding:8px 10px; background:rgba(255,255,255,0.03); border-radius:8px; border:1px solid rgba(255,255,255,0.06)">
              <span style="font-size:11px; color:#94a3b8; white-space:nowrap">⚡ Profil instalacji</span>
              <select id="roi-profile-select" style="flex:1; min-width:180px; background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.15); border-radius:6px; color:#fff; padding:6px 10px; font-size:12px; cursor:pointer" onchange="this.getRootNode().host._onRoiProfileChange(this.value)">
                <option value="real" style="background:#1e293b">📊 Moja instalacja (realne dane)</option>
                <option value="5kw" style="background:#1e293b">☀️ 5 kW PV + 5 kWh bat (~35 000 zł)</option>
                <option value="10kw" style="background:#1e293b">☀️ 10 kW PV + 10 kWh bat (~55 000 zł)</option>
                <option value="15kw" style="background:#1e293b">☀️ 15 kW PV + 15 kWh bat (~75 000 zł)</option>
                <option value="custom" style="background:#1e293b">🔧 Niestandardowa...</option>
              </select>
              <span id="roi-profile-desc" style="font-size:9px; color:#64748b; width:100%"></span>
            </div>
            <div id="roi-custom-panel" style="display:none; margin-bottom:14px; padding:10px; background:rgba(255,255,255,0.03); border-radius:8px; border:1px solid rgba(168,85,247,0.2)">
              <div style="font-size:10px; color:#a855f7; font-weight:600; margin-bottom:8px">🔧 Parametry niestandardowe</div>
              <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; font-size:11px">
                <label style="color:#94a3b8">Moc PV (kWp):</label>
                <input id="roi-custom-pv" type="number" value="10" min="1" max="50" step="0.5" style="background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.15); border-radius:4px; color:#fff; padding:4px 8px; font-size:12px; text-align:right" onchange="this.getRootNode().host._onRoiCustomChange()">
                <label style="color:#94a3b8">Magazyn (kWh):</label>
                <input id="roi-custom-bat" type="number" value="10" min="0" max="50" step="0.5" style="background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.15); border-radius:4px; color:#fff; padding:4px 8px; font-size:12px; text-align:right" onchange="this.getRootNode().host._onRoiCustomChange()">
                <label style="color:#94a3b8">Moc ładowania (kW):</label>
                <input id="roi-custom-rate" type="number" value="3" min="1" max="10" step="0.5" style="background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.15); border-radius:4px; color:#fff; padding:4px 8px; font-size:12px; text-align:right" onchange="this.getRootNode().host._onRoiCustomChange()">
                <label style="color:#94a3b8">Roczna prod. (kWh/kWp):</label>
                <input id="roi-custom-yield" type="number" value="1000" min="500" max="1500" step="50" style="background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.15); border-radius:4px; color:#fff; padding:4px 8px; font-size:12px; text-align:right" onchange="this.getRootNode().host._onRoiCustomChange()">
              </div>
              <div style="font-size:9px; color:#64748b; margin-top:6px">💡 Zużycie roczne pobierane z Twoich czujników HA</div>
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

          <!-- Today's Wind Calendar Status (live from backend) -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">📅 Dzisiejszy dzień — status kalendarza wiatrowego</div>
            <div style="font-size:10px; color:#94a3b8; margin-bottom:10px">Aktualizacja na żywo z silnika WindCalendar (co 30s). Zamknięcie dnia o północy.</div>
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(120px, 1fr)); gap:8px" id="wind-cal-today-grid">
              <div style="background:rgba(0,212,255,0.06); border:1px solid rgba(0,212,255,0.12); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase">⏱️ Próbki</div>
                <div style="font-size:18px; font-weight:800; color:#00d4ff; margin-top:4px" id="wc-today-samples">0</div>
              </div>
              <div style="background:rgba(46,204,113,0.06); border:1px solid rgba(46,204,113,0.12); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase">💨 Średni wiatr</div>
                <div style="font-size:18px; font-weight:800; color:#2ecc71; margin-top:4px" id="wc-today-avgwind">— km/h</div>
              </div>
              <div style="background:rgba(247,183,49,0.06); border:1px solid rgba(247,183,49,0.12); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase">⚡ Estymacja kWh</div>
                <div style="font-size:18px; font-weight:800; color:#f7b731; margin-top:4px" id="wc-today-kwh">—</div>
              </div>
              <div style="background:rgba(46,204,113,0.06); border:1px solid rgba(46,204,113,0.12); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase">💰 Przychód</div>
                <div style="font-size:18px; font-weight:800; color:#2ecc71; margin-top:4px" id="wc-today-revenue">— zł</div>
              </div>
              <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase">📊 Produktywność</div>
                <div style="font-size:18px; font-weight:800; color:#e0e6ed; margin-top:4px" id="wc-today-productive">—%</div>
              </div>
            </div>
          </div>

          <!-- ═══ Period Navigator ═══ -->
          <div class="card" style="margin-bottom:12px">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px">
              <div style="display:flex; gap:4px">
                <button class="hist-period-btn active" id="wc-period-week" onclick="this.getRootNode().host._switchWindCalPeriod('week')">📆 Tydzień</button>
                <button class="hist-period-btn" id="wc-period-month" onclick="this.getRootNode().host._switchWindCalPeriod('month')">🗓️ Miesiąc</button>
                <button class="hist-period-btn" id="wc-period-year" onclick="this.getRootNode().host._switchWindCalPeriod('year')">📊 Rok</button>
                <button class="hist-period-btn" id="wc-period-all" onclick="this.getRootNode().host._switchWindCalPeriod('all')">🗂️ Wszystko</button>
              </div>
              <div style="display:flex; align-items:center; gap:6px">
                <button class="hist-nav-btn hist-nav-arrow" onclick="this.getRootNode().host._windCalNavigate(-1)">◀</button>
                <span style="font-size:13px; font-weight:700; color:#e0e6ed; min-width:160px; text-align:center" id="wc-period-label">—</span>
                <button class="hist-nav-btn hist-nav-arrow" onclick="this.getRootNode().host._windCalNavigate(1)">▶</button>
                <button class="hist-nav-btn" onclick="this.getRootNode().host._windCalNavigate(0)" style="font-size:10px; padding:4px 10px">Dziś</button>
              </div>
            </div>
          </div>

          <!-- ═══ KPI Cards for selected period ═══ -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">📊 Podsumowanie wybranego okresu</div>
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(130px, 1fr)); gap:8px; margin-top:8px" id="wc-kpi-grid">
              <div style="background:rgba(0,212,255,0.06); border:1px solid rgba(0,212,255,0.12); border-radius:12px; padding:12px; text-align:center">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase">⚡ Produkcja</div>
                <div style="font-size:22px; font-weight:900; color:#00d4ff; margin-top:4px" id="wc-kpi-kwh">—</div>
                <div style="font-size:9px; color:#94a3b8">kWh</div>
              </div>
              <div style="background:rgba(46,204,113,0.06); border:1px solid rgba(46,204,113,0.12); border-radius:12px; padding:12px; text-align:center">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase">💰 Przychód</div>
                <div style="font-size:22px; font-weight:900; color:#2ecc71; margin-top:4px" id="wc-kpi-revenue">—</div>
                <div style="font-size:9px; color:#94a3b8">zł</div>
              </div>
              <div style="background:rgba(247,183,49,0.06); border:1px solid rgba(247,183,49,0.12); border-radius:12px; padding:12px; text-align:center">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase">📅 Dni produkcyjne</div>
                <div style="font-size:22px; font-weight:900; color:#f7b731; margin-top:4px" id="wc-kpi-days">—</div>
                <div style="font-size:9px; color:#94a3b8" id="wc-kpi-days-total">/ — łącznie</div>
              </div>
              <div style="background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08); border-radius:12px; padding:12px; text-align:center">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase">📊 Capacity Factor</div>
                <div style="font-size:22px; font-weight:900; color:#e0e6ed; margin-top:4px" id="wc-kpi-cf">—%</div>
                <div style="font-size:9px; color:#94a3b8">średni</div>
              </div>
              <div style="background:rgba(46,204,113,0.04); border:1px solid rgba(46,204,113,0.08); border-radius:12px; padding:12px; text-align:center">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase">🏆 Najlepszy dzień</div>
                <div style="font-size:14px; font-weight:800; color:#2ecc71; margin-top:4px" id="wc-kpi-best">—</div>
                <div style="font-size:9px; color:#94a3b8" id="wc-kpi-best-date">—</div>
              </div>
              <div style="background:rgba(231,76,60,0.04); border:1px solid rgba(231,76,60,0.08); border-radius:12px; padding:12px; text-align:center">
                <div style="font-size:9px; color:#64748b; text-transform:uppercase">💨 Średni wiatr</div>
                <div style="font-size:14px; font-weight:800; color:#94a3b8; margin-top:4px" id="wc-kpi-wind">—</div>
                <div style="font-size:9px; color:#94a3b8">km/h</div>
              </div>
            </div>
          </div>

          <!-- ═══ Daily Production Bar Chart ═══ -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">📊 Produkcja dzienna (kWh)</div>
            <div style="font-size:10px; color:#94a3b8; margin-bottom:8px" id="wc-chart-subtitle">Wybierz okres aby zobaczyć dane</div>
            <div id="wc-daily-chart" style="display:flex; align-items:flex-end; gap:2px; height:160px; padding:8px 0; overflow-x:auto">
              <div style="color:#64748b; font-size:11px; text-align:center; width:100%">Ładowanie danych kalendarza wiatrowego...</div>
            </div>
            <div id="wc-chart-legend" style="font-size:9px; color:#64748b; text-align:center; margin-top:6px">
              <span style="color:#2ecc71">■</span> Zyskowny &nbsp;
              <span style="color:#e74c3c">■</span> Poniżej progu &nbsp;
              <span style="color:#475569">■</span> Brak produkcji
            </div>
          </div>

          <!-- ═══ Wind Distribution (Beaufort histogram) ═══ -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">🌡️ Rozkład dni wg klasy wiatru</div>
            <div style="font-size:10px; color:#94a3b8; margin-bottom:10px">Ile dni w wybranym okresie miało dany poziom wiatru</div>
            <div id="wc-distribution" style="display:grid; grid-template-columns:repeat(5, 1fr); gap:6px">
              <div style="background:rgba(100,116,139,0.1); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:22px">🍃</div>
                <div style="font-size:10px; font-weight:700; color:#64748b">Cisza</div>
                <div style="font-size:9px; color:#94a3b8">&lt; 2 m/s</div>
                <div style="font-size:18px; font-weight:900; color:#64748b; margin-top:4px" id="wc-dist-calm">—</div>
                <div style="font-size:9px; color:#94a3b8">dni</div>
              </div>
              <div style="background:rgba(46,204,113,0.08); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:22px">🌿</div>
                <div style="font-size:10px; font-weight:700; color:#2ecc71">Słaby</div>
                <div style="font-size:9px; color:#94a3b8">2–4 m/s</div>
                <div style="font-size:18px; font-weight:900; color:#2ecc71; margin-top:4px" id="wc-dist-light">—</div>
                <div style="font-size:9px; color:#94a3b8">dni</div>
              </div>
              <div style="background:rgba(247,183,49,0.08); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:22px">🌬️</div>
                <div style="font-size:10px; font-weight:700; color:#f7b731">Umiarkowany</div>
                <div style="font-size:9px; color:#94a3b8">4–6 m/s</div>
                <div style="font-size:18px; font-weight:900; color:#f7b731; margin-top:4px" id="wc-dist-moderate">—</div>
                <div style="font-size:9px; color:#94a3b8">dni</div>
              </div>
              <div style="background:rgba(231,76,60,0.06); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:22px">💨</div>
                <div style="font-size:10px; font-weight:700; color:#e67e22">Silny</div>
                <div style="font-size:9px; color:#94a3b8">6–8 m/s</div>
                <div style="font-size:18px; font-weight:900; color:#e67e22; margin-top:4px" id="wc-dist-strong">—</div>
                <div style="font-size:9px; color:#94a3b8">dni</div>
              </div>
              <div style="background:rgba(192,57,43,0.08); border-radius:10px; padding:10px; text-align:center">
                <div style="font-size:22px">🌪️</div>
                <div style="font-size:10px; font-weight:700; color:#c0392b">Bardzo silny</div>
                <div style="font-size:9px; color:#94a3b8">&gt; 8 m/s</div>
                <div style="font-size:18px; font-weight:900; color:#c0392b; margin-top:4px" id="wc-dist-vstrong">—</div>
                <div style="font-size:9px; color:#94a3b8">dni</div>
              </div>
            </div>
          </div>

          <!-- ═══ ROI / Profitability from Calendar ═══ -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">💰 Analiza opłacalności — na podstawie kalendarza</div>
            <div style="font-size:10px; color:#94a3b8; margin-bottom:10px">Obliczenia na podstawie rzeczywistych danych dziennych, nie średniej rocznej</div>
            <div id="wc-roi-content" style="padding:8px">
              <div style="text-align:center; color:#64748b; font-size:12px">Ładowanie danych kalendarza...</div>
            </div>
          </div>

          <!-- ═══ Dynamic Recommendation ═══ -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">💡 Rekomendacja — na podstawie kalendarza wiatrowego</div>
            <div id="wc-recommendation" style="padding:12px">
              <div style="text-align:center; color:#64748b; font-size:12px">Oczekiwanie na dane kalendarza wiatrowego...</div>
            </div>
          </div>

          <!-- Beaufort scale reference (zachowane) -->
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

          <!-- ═══ PEAK SELL CONFIGURATION ═══ -->
          <div class="card" style="margin-bottom:14px; border:1px solid rgba(247,183,49,0.2); background:linear-gradient(135deg, rgba(247,183,49,0.04) 0%, rgba(231,76,60,0.03) 100%)">
            <div class="card-title" style="display:flex; justify-content:space-between; align-items:center">
              <span>💰 Sprzedaż energii w szczycie (Peak Sell)</span>
              <span id="ap-peak-sell-badge" style="font-size:11px; padding:3px 10px; border-radius:20px; background:rgba(247,183,49,0.15); color:#f7b731; font-weight:600">50%</span>
            </div>
            <div style="font-size:11px; color:#94a3b8; margin-bottom:12px; line-height:1.5">
              Ile % baterii aktywnie sprzedać do sieci w najdroższym szczycie popołudniowym (AFTERNOON_PEAK)?<br>
              <span style="color:#64748b">Reszta zostanie zarezerwowana na zasilanie domu. 0% = wyłączone.</span>
            </div>
            <div style="display:flex; align-items:center; gap:12px; margin-bottom:8px">
              <span style="font-size:10px; color:#64748b; min-width:20px">0%</span>
              <input type="range" id="ap-peak-sell-slider" min="0" max="80" step="5" value="50"
                style="flex:1; accent-color:#f7b731; height:6px; cursor:pointer"
                oninput="this.getRootNode().host._onPeakSellSliderChange(this.value)"
                onchange="this.getRootNode().host._savePeakSellPercent(this.value)" />
              <span style="font-size:10px; color:#64748b; min-width:28px">80%</span>
            </div>
            <div style="display:grid; grid-template-columns:repeat(3, 1fr); gap:6px; margin-top:8px">
              <div style="text-align:center; padding:6px; border-radius:8px; background:rgba(255,255,255,0.03)">
                <div style="font-size:9px; color:#64748b">Na sprzedaż</div>
                <div id="ap-peak-sell-kwh" style="font-size:14px; font-weight:700; color:#f7b731">—</div>
              </div>
              <div style="text-align:center; padding:6px; border-radius:8px; background:rgba(255,255,255,0.03)">
                <div style="font-size:9px; color:#64748b">Rezerwa dom</div>
                <div id="ap-peak-sell-reserve" style="font-size:14px; font-weight:700; color:#2ecc71">—</div>
              </div>
              <div style="text-align:center; padding:6px; border-radius:8px; background:rgba(255,255,255,0.03)">
                <div style="font-size:9px; color:#64748b">~Zarobek</div>
                <div id="ap-peak-sell-revenue" style="font-size:14px; font-weight:700; color:#00d4ff">—</div>
              </div>
            </div>
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


        <!-- ═══════ TAB: FORECAST (Prognoza Solarna AI) ═══════ -->
        <div class="tab-content" data-tab="forecast">

          <!-- §1 HERO + KPI -->
          <div class="card fc-hero" style="margin-bottom:12px">
            <div style="position:absolute; top:0; left:0; right:0; bottom:0; opacity:0.03; font-size:120px; display:flex; align-items:center; justify-content:center">☀️</div>
            <div style="position:relative">
              <div class="card-title">☀️ Prognoza Solarna AI — Smarting HOME</div>
              <div style="font-size:11px; color:#94a3b8; margin-bottom:4px">Dokładna prognoza produkcji PV + inteligentne rekomendacje sterowania energią</div>
              <div style="font-size:10px; color:#475569">Dane: Open-Meteo Solar Radiation • Kalibracja Kalman • Silnik Decyzji</div>
            </div>
            <div class="fc-kpi-grid">
              <div class="fc-kpi">
                <div class="fc-kpi-label">☀️ Prognoza na dziś</div>
                <div class="fc-kpi-value" style="color:#f7b731" id="fc-kpi-today">— <span style="font-size:14px; font-weight:400">kWh</span></div>
                <div class="fc-kpi-sub" id="fc-kpi-today-sub">ładowanie danych...</div>
              </div>
              <div class="fc-kpi">
                <div class="fc-kpi-label">⚡ Peak PV</div>
                <div class="fc-kpi-value" style="color:#2ecc71" id="fc-kpi-peak">—</div>
                <div class="fc-kpi-sub" id="fc-kpi-peak-sub">okno szczytowej produkcji</div>
              </div>
              <div class="fc-kpi">
                <div class="fc-kpi-label">🎯 Tryb strategii</div>
                <div class="fc-kpi-value" style="color:#00d4ff; font-size:18px" id="fc-kpi-strategy">AUTARKIA</div>
                <div class="fc-kpi-sub">optymalizacja energii</div>
              </div>
            </div>
          </div>

          <!-- §2 WEEKLY CHART -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">📊 Prognoza Dzienna — 7 Dni</div>
            <div style="font-size:10px; color:#64748b; margin-bottom:4px">
              Tydzień: <span id="fc-week-total" style="color:#f7b731; font-weight:700">—</span> kWh ·
              Średnia: <span id="fc-week-avg" style="color:#2ecc71; font-weight:700">—</span> kWh/dzień
            </div>
            <div class="fc-week-bars" id="fc-week-bars">
              <div style="text-align:center; color:#64748b; padding:40px; width:100%">⏳ Pobieranie prognozy...</div>
            </div>
          </div>

          <!-- §3 HOURLY CURVE + TIMELINE -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">📈 Prognoza Godzinowa — Dziś</div>
            <div style="font-size:10px; color:#64748b; margin-bottom:8px">Prognoza vs rzeczywista produkcja • Marker „teraz" • Adnotacje decyzji</div>
            <div id="fc-hourly-curve" style="margin-bottom:12px"></div>
            <div style="display:flex; justify-content:space-between; padding:0 4px; margin-bottom:6px">
              <span style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">Godz.</span>
              <span style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">Moc</span>
              <span style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px" style="min-width:55px; text-align:right">kW</span>
              <span style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">°C</span>
            </div>
            <div id="fc-hourly-list" style="max-height:400px; overflow-y:auto; border-radius:8px">
              <div style="text-align:center; color:#64748b; padding:20px">⏳ Ładowanie...</div>
            </div>
          </div>

          <!-- §4 DECISION ENGINE -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">🧠 Silnik Decyzji — Smarting HOME</div>
            <div style="font-size:10px; color:#94a3b8; margin-bottom:12px">Inteligentne rekomendacje na bazie prognozy PV, cen energii i stanu baterii</div>
            <div id="fc-decisions">
              <div style="text-align:center; color:#64748b; padding:20px">⏳ Generowanie rekomendacji...</div>
            </div>
          </div>

          <!-- §5 STRATEGY MODES -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">🎯 Tryb Strategii Energetycznej</div>
            <div style="font-size:10px; color:#94a3b8; margin-bottom:12px">Wybierz tryb optymalizacji — wpływa na rekomendacje silnika decyzji i logikę Autopilota</div>
            <div class="fc-strategy-grid">
              <div class="fc-strategy-btn" id="fc-str-zysk" onclick="this.getRootNode().host._setForecastStrategy('MAX_ZYSK')">
                <div class="fc-strategy-icon">💰</div>
                <div class="fc-strategy-name">MAX ZYSK</div>
                <div class="fc-strategy-desc">Priorytet sprzedaży energii w godzinach szczytowych cen</div>
              </div>
              <div class="fc-strategy-btn active" id="fc-str-autarkia" onclick="this.getRootNode().host._setForecastStrategy('AUTARKIA')">
                <div class="fc-strategy-icon">🏠</div>
                <div class="fc-strategy-name">AUTARKIA</div>
                <div class="fc-strategy-desc">Maksymalizacja samodzielnego zużycia i niezależności</div>
              </div>
              <div class="fc-strategy-btn" id="fc-str-eco" onclick="this.getRootNode().host._setForecastStrategy('ECO')">
                <div class="fc-strategy-icon">🌱</div>
                <div class="fc-strategy-name">ECO DOM</div>
                <div class="fc-strategy-desc">Balans domowego komfortu i oszczędności</div>
              </div>
            </div>
          </div>

          <!-- §5b PV SYSTEM CONFIG -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">⚙️ Parametry Instalacji PV</div>
            <div style="font-size:10px; color:#94a3b8; margin-bottom:12px" id="fc-pv-config-subtitle">Konfiguracja pobrana z Przeglądu → Produkcja PV</div>
            <div id="fc-pv-config-area">
              <!-- Dynamically populated by _renderForecastPvConfig() -->
            </div>
            <div style="margin-top:10px; display:flex; align-items:center; gap:12px">
              <div style="font-size:10px; color:#94a3b8; flex:1" id="fc-cfg-summary">Szacunkowa roczna produkcja: — kWh</div>
              <button style="background:linear-gradient(135deg,#f7b731,#e67e22); border:none; color:#0f172a; padding:8px 18px; border-radius:8px; font-size:11px; font-weight:700; cursor:pointer; letter-spacing:0.3px" onclick="this.getRootNode().host._saveForecastConfig(true)">🔄 PRZELICZ</button>
            </div>
          </div>

          <!-- §6 AUTO-CALIBRATION -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">🔧 Autokalibracja — Filtr Kalman</div>
            <div style="font-size:10px; color:#94a3b8; margin-bottom:8px">System uczy się Twojej instalacji porównując prognozy z rzeczywistą produkcją</div>
            <div id="fc-calibration">
              <div class="fc-calib-bar">
                <span class="fc-calib-label">Współczynnik korekcji</span>
                <span class="fc-calib-value" id="fc-calib-factor">1.00</span>
              </div>
              <div class="fc-calib-bar">
                <span class="fc-calib-label">Próbki kalibracji</span>
                <span class="fc-calib-value" id="fc-calib-samples" style="color:#00d4ff">0</span>
              </div>
              <div class="fc-calib-bar">
                <span class="fc-calib-label">Dokładność (MAPE)</span>
                <span class="fc-calib-value" id="fc-calib-mape" style="color:#f7b731">—%</span>
              </div>
              <div style="margin-top:8px; display:flex; gap:8px">
                <div style="flex:1; background:rgba(255,255,255,0.04); border-radius:8px; height:60px; position:relative; overflow:hidden" id="fc-calib-chart"></div>
              </div>
            </div>
          </div>

          <!-- §7 INTEGRATIONS STATUS -->
          <div class="card" style="margin-bottom:12px">
            <div class="card-title">🔗 Podłączone Integracje</div>
            <div class="fc-integ-grid" id="fc-integrations">
              <div class="fc-integ-item">
                <div class="fc-integ-icon">⚡</div>
                <div class="fc-integ-name">Falownik</div>
                <div class="fc-integ-status" id="fc-integ-inverter" style="color:#64748b">wykrywanie...</div>
              </div>
              <div class="fc-integ-item">
                <div class="fc-integ-icon">🏠</div>
                <div class="fc-integ-name">Home Assistant</div>
                <div class="fc-integ-status" style="color:#2ecc71">✅ Połączono</div>
              </div>
              <div class="fc-integ-item">
                <div class="fc-integ-icon">🔋</div>
                <div class="fc-integ-name">Bateria</div>
                <div class="fc-integ-status" id="fc-integ-battery" style="color:#64748b">wykrywanie...</div>
              </div>
              <div class="fc-integ-item">
                <div class="fc-integ-icon">💰</div>
                <div class="fc-integ-name">Ceny Energii</div>
                <div class="fc-integ-status" id="fc-integ-prices" style="color:#64748b">wykrywanie...</div>
              </div>
              <div class="fc-integ-item">
                <div class="fc-integ-icon">🌤️</div>
                <div class="fc-integ-name">Open-Meteo</div>
                <div class="fc-integ-status" id="fc-integ-meteo" style="color:#64748b">—</div>
              </div>
              <div class="fc-integ-item">
                <div class="fc-integ-icon">📡</div>
                <div class="fc-integ-name">Forecast.Solar</div>
                <div class="fc-integ-status" id="fc-integ-fcsolar" style="color:#64748b">wykrywanie...</div>
              </div>
              <div class="fc-integ-item">
                <div class="fc-integ-icon">🌡️</div>
                <div class="fc-integ-name">Ecowitt Local</div>
                <div class="fc-integ-status" id="fc-integ-ecowitt" style="color:#64748b">wykrywanie...</div>
              </div>
            </div>
          </div>

        </div>

        <!-- ═══════ TAB: ALERTS & ANOMALIES ═══════ -->
        <div class="tab-content" data-tab="alerts">

          <!-- §1 STATUS BAR -->
          <div class="alert-status-bar ok" id="alert-status-bar">
            <div class="alert-status-icon" id="alert-status-icon">🟢</div>
            <div class="alert-status-info">
              <div class="alert-status-label">Status instalacji</div>
              <div class="alert-status-text" id="alert-status-text">Instalacja OK</div>
              <div class="alert-status-meta">
                <span>🔔 Aktywne alerty: <strong id="alert-count">0</strong></span>
                <span>⏱️ Ostatni odczyt: <strong id="alert-last-update">—</strong></span>
                <span>📊 24h ostrzeżeń: <strong id="alert-24h-count">0</strong></span>
              </div>
            </div>
            <div class="health-score-wrap">
              <div class="health-score-ring">
                <svg viewBox="0 0 80 80" width="80" height="80">
                  <circle class="ring-bg" cx="40" cy="40" r="34" />
                  <circle class="ring-fg" id="health-ring-fg" cx="40" cy="40" r="34"
                    stroke="#2ecc71" stroke-dasharray="213.6" stroke-dashoffset="0" />
                </svg>
                <div class="health-score-val" id="health-score-val">—</div>
              </div>
              <div class="health-score-label">Zdrowie</div>
            </div>
          </div>

          <!-- §2 SOURCE TILES -->
          <div class="alert-tiles" id="alert-tiles">
            <!-- Falownik -->
            <div class="alert-tile ok" id="alert-tile-inv" onclick="this.getRootNode().host._showAlertTileDetail('inv')">
              <div class="alert-tile-header">
                <span class="alert-tile-icon">🔧</span>
                <span class="alert-tile-name">Falownik</span>
                <div class="alert-tile-dot"></div>
              </div>
              <div class="alert-tile-values">
                <div class="alert-tile-row"><span class="atl">Status</span><span class="atv" id="at-inv-status">—</span></div>
                <div class="alert-tile-row"><span class="atl">Temp.</span><span class="atv" id="at-inv-temp">—</span></div>
                <div class="alert-tile-row"><span class="atl">Kontakt</span><span class="atv" id="at-inv-contact">—</span></div>
              </div>
              <div class="alert-tile-issue" id="at-inv-issue" style="display:none"></div>
            </div>

            <!-- PV / Stringi -->
            <div class="alert-tile ok" id="alert-tile-pv" onclick="this.getRootNode().host._showAlertTileDetail('pv')">
              <div class="alert-tile-header">
                <span class="alert-tile-icon">☀️</span>
                <span class="alert-tile-name">PV / Stringi</span>
                <div class="alert-tile-dot"></div>
              </div>
              <div class="alert-tile-values">
                <div class="alert-tile-row"><span class="atl">Produkcja</span><span class="atv" id="at-pv-power">—</span></div>
                <div class="alert-tile-row"><span class="atl">Oczekiwana</span><span class="atv" id="at-pv-expected">—</span></div>
                <div class="alert-tile-row"><span class="atl">Δ Stringi</span><span class="atv" id="at-pv-delta">—</span></div>
              </div>
              <div class="alert-tile-issue" id="at-pv-issue" style="display:none"></div>
            </div>

            <!-- Bateria -->
            <div class="alert-tile ok" id="alert-tile-bat" onclick="this.getRootNode().host._showAlertTileDetail('bat')">
              <div class="alert-tile-header">
                <span class="alert-tile-icon">🔋</span>
                <span class="alert-tile-name">Bateria</span>
                <div class="alert-tile-dot"></div>
              </div>
              <div class="alert-tile-values">
                <div class="alert-tile-row"><span class="atl">SOC</span><span class="atv" id="at-bat-soc">—</span></div>
                <div class="alert-tile-row"><span class="atl">Moc</span><span class="atv" id="at-bat-power">—</span></div>
                <div class="alert-tile-row"><span class="atl">Temp.</span><span class="atv" id="at-bat-temp">—</span></div>
              </div>
              <div class="alert-tile-issue" id="at-bat-issue" style="display:none"></div>
            </div>

            <!-- Sieć -->
            <div class="alert-tile ok" id="alert-tile-grid" onclick="this.getRootNode().host._showAlertTileDetail('grid')">
              <div class="alert-tile-header">
                <span class="alert-tile-icon">⚡</span>
                <span class="alert-tile-name">Sieć</span>
                <div class="alert-tile-dot"></div>
              </div>
              <div class="alert-tile-values">
                <div class="alert-tile-row"><span class="atl">L1</span><span class="atv" id="at-grid-l1">—</span></div>
                <div class="alert-tile-row"><span class="atl">L2</span><span class="atv" id="at-grid-l2">—</span></div>
                <div class="alert-tile-row"><span class="atl">L3</span><span class="atv" id="at-grid-l3">—</span></div>
              </div>
              <div class="alert-tile-issue" id="at-grid-issue" style="display:none"></div>
            </div>

            <!-- Pomiar / CT -->
            <div class="alert-tile ok" id="alert-tile-meter" onclick="this.getRootNode().host._showAlertTileDetail('meter')">
              <div class="alert-tile-header">
                <span class="alert-tile-icon">📊</span>
                <span class="alert-tile-name">Pomiar / CT</span>
                <div class="alert-tile-dot"></div>
              </div>
              <div class="alert-tile-values">
                <div class="alert-tile-row"><span class="atl">Import</span><span class="atv" id="at-meter-import">—</span></div>
                <div class="alert-tile-row"><span class="atl">Eksport</span><span class="atv" id="at-meter-export">—</span></div>
                <div class="alert-tile-row"><span class="atl">Status</span><span class="atv" id="at-meter-status">OK</span></div>
              </div>
              <div class="alert-tile-issue" id="at-meter-issue" style="display:none"></div>
            </div>

            <!-- Komunikacja -->
            <div class="alert-tile ok" id="alert-tile-comm" onclick="this.getRootNode().host._showAlertTileDetail('comm')">
              <div class="alert-tile-header">
                <span class="alert-tile-icon">📡</span>
                <span class="alert-tile-name">Komunikacja</span>
                <div class="alert-tile-dot"></div>
              </div>
              <div class="alert-tile-values">
                <div class="alert-tile-row"><span class="atl">Modbus</span><span class="atv" id="at-comm-modbus">—</span></div>
                <div class="alert-tile-row"><span class="atl">HA</span><span class="atv" id="at-comm-ha">OK</span></div>
                <div class="alert-tile-row"><span class="atl">Update</span><span class="atv" id="at-comm-update">—</span></div>
              </div>
              <div class="alert-tile-issue" id="at-comm-issue" style="display:none"></div>
            </div>
          </div>

          <!-- §3 ACTIVE ALERTS -->
          <div class="card" style="margin-bottom:14px">
            <div class="alert-list-header">
              <span class="alert-list-title">🔔 Aktywne alerty</span>
              <span class="alert-list-count" id="alert-active-count">0 alertów</span>
            </div>
            <div id="alert-list-items">
              <div class="alert-empty">
                <div class="alert-empty-icon">✅</div>
                <div>Brak aktywnych alertów — instalacja działa prawidłowo</div>
              </div>
            </div>
          </div>

          <!-- §4 HEALTH BREAKDOWN -->
          <div class="card" style="margin-bottom:14px">
            <div class="card-title">🧮 Zdrowie instalacji — szczegóły</div>
            <div style="font-size:10px; color:#94a3b8; margin-bottom:10px">Składowe Health Score kalkulowane na żywo z danych sensorycznych</div>
            <div class="health-breakdown" id="health-breakdown">
              <div class="health-factor">
                <div class="health-factor-icon">📡</div>
                <div class="health-factor-info">
                  <div class="health-factor-name">Dostępność danych</div>
                  <div class="health-factor-val" id="hf-data">—</div>
                  <div class="health-factor-bar"><div class="health-factor-bar-fill" id="hf-data-bar" style="width:0%;background:#2ecc71"></div></div>
                </div>
              </div>
              <div class="health-factor">
                <div class="health-factor-icon">🔧</div>
                <div class="health-factor-info">
                  <div class="health-factor-name">Falownik</div>
                  <div class="health-factor-val" id="hf-inv">—</div>
                  <div class="health-factor-bar"><div class="health-factor-bar-fill" id="hf-inv-bar" style="width:0%;background:#2ecc71"></div></div>
                </div>
              </div>
              <div class="health-factor">
                <div class="health-factor-icon">☀️</div>
                <div class="health-factor-info">
                  <div class="health-factor-name">Produkcja PV</div>
                  <div class="health-factor-val" id="hf-pv">—</div>
                  <div class="health-factor-bar"><div class="health-factor-bar-fill" id="hf-pv-bar" style="width:0%;background:#2ecc71"></div></div>
                </div>
              </div>
              <div class="health-factor">
                <div class="health-factor-icon">🔋</div>
                <div class="health-factor-info">
                  <div class="health-factor-name">Bateria / BMS</div>
                  <div class="health-factor-val" id="hf-bat">—</div>
                  <div class="health-factor-bar"><div class="health-factor-bar-fill" id="hf-bat-bar" style="width:0%;background:#2ecc71"></div></div>
                </div>
              </div>
              <div class="health-factor">
                <div class="health-factor-icon">⚡</div>
                <div class="health-factor-info">
                  <div class="health-factor-name">Jakość sieci</div>
                  <div class="health-factor-val" id="hf-grid">—</div>
                  <div class="health-factor-bar"><div class="health-factor-bar-fill" id="hf-grid-bar" style="width:0%;background:#2ecc71"></div></div>
                </div>
              </div>
              <div class="health-factor">
                <div class="health-factor-icon">🔔</div>
                <div class="health-factor-info">
                  <div class="health-factor-name">Alarmy</div>
                  <div class="health-factor-val" id="hf-alarms">—</div>
                  <div class="health-factor-bar"><div class="health-factor-bar-fill" id="hf-alarms-bar" style="width:0%;background:#2ecc71"></div></div>
                </div>
              </div>
            </div>
          </div>

          <!-- §5 ALERT HISTORY (last 20) -->
          <div class="card">
            <div class="card-title">📋 Historia zdarzeń (ostatnie 24h)</div>
            <div style="font-size:10px; color:#94a3b8; margin-bottom:10px">Kliknij zdarzenie aby zobaczyć diagnozę i rekomendację</div>
            <div id="alert-history-items" style="max-height:300px; overflow-y:auto">
              <div style="color:#64748b; text-align:center; padding:16px; font-size:12px">Brak zarejestrowanych zdarzeń</div>
            </div>
          </div>

          <!-- §6 NOTIFICATION CONFIG -->
          <div class="card">
            <div class="card-title">🔔 Powiadomienia</div>
            <div style="font-size:10px; color:#94a3b8; margin-bottom:12px">Otrzymuj alerty o awariach na telefon, email lub w Home Assistant</div>

            <!-- Master switch -->
            <div style="display:flex; align-items:center; justify-content:space-between; padding:10px 14px; background:rgba(255,255,255,0.03); border-radius:10px; margin-bottom:14px">
              <div>
                <div style="font-size:13px; font-weight:600; color:#e2e8f0">Powiadomienia aktywne</div>
                <div style="font-size:10px; color:#64748b">Włącz aby otrzymywać alerty</div>
              </div>
              <label class="toggle-switch">
                <input type="checkbox" id="notif-master" onchange="this.getRootNode().host._onNotifToggle()">
                <span class="toggle-slider"></span>
              </label>
            </div>

            <!-- Channels -->
            <div id="notif-channels-wrap" style="display:none">
              <div style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px">Kanały dostarczania</div>

              <!-- HA Push -->
              <div style="padding:10px 14px; background:rgba(255,255,255,0.02); border-radius:8px; margin-bottom:8px; border-left:3px solid #00d4ff">
                <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:6px">
                  <div style="font-size:12px; font-weight:600; color:#e2e8f0">📱 Push (HA App)</div>
                  <label class="toggle-switch sm"><input type="checkbox" id="notif-ch-push" onchange="this.getRootNode().host._onNotifToggle()"><span class="toggle-slider"></span></label>
                </div>
                <div id="notif-push-entity-wrap" style="display:none">
                  <div id="notif-push-devices-list"></div>
                  <button onclick="this.getRootNode().host._addPushDevice()" style="width:100%; padding:6px 10px; background:rgba(0,212,255,0.08); border:1px dashed rgba(0,212,255,0.3); border-radius:6px; color:#00d4ff; font-size:11px; cursor:pointer; margin-top:6px; transition:background 0.2s" onmouseover="this.style.background='rgba(0,212,255,0.15)'" onmouseout="this.style.background='rgba(0,212,255,0.08)'">+ Dodaj urządzenie</button>
                  <div style="font-size:9px; color:#64748b; margin-top:3px">Wybierz urządzenia z aplikacji HA Companion</div>
                </div>
              </div>

              <!-- Persistent -->
              <div style="padding:10px 14px; background:rgba(255,255,255,0.02); border-radius:8px; margin-bottom:8px; border-left:3px solid #2ecc71">
                <div style="display:flex; align-items:center; justify-content:space-between">
                  <div style="font-size:12px; font-weight:600; color:#e2e8f0">📌 Powiadomienie HA</div>
                  <label class="toggle-switch sm"><input type="checkbox" id="notif-ch-persistent" onchange="this.getRootNode().host._onNotifToggle()"><span class="toggle-slider"></span></label>
                </div>
                <div style="font-size:9px; color:#64748b; margin-top:3px">Pojawia się w panelu powiadomień Home Assistant</div>
              </div>

              <!-- SMS -->
              <div style="padding:10px 14px; background:rgba(255,255,255,0.02); border-radius:8px; margin-bottom:8px; border-left:3px solid #f59e0b">
                <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:6px">
                  <div style="font-size:12px; font-weight:600; color:#e2e8f0">📱 SMS</div>
                  <label class="toggle-switch sm"><input type="checkbox" id="notif-ch-sms" onchange="this.getRootNode().host._onNotifToggle()"><span class="toggle-slider"></span></label>
                </div>
                <div id="notif-sms-wrap" style="display:none">
                  <input type="tel" id="notif-phone" placeholder="+48 600 123 456" style="width:100%; padding:6px 10px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); border-radius:6px; color:#e2e8f0; font-size:11px; outline:none">
                  <div style="font-size:9px; color:#64748b; margin-top:3px">Numer z kierunkowym kraju (np. +48...)</div>
                </div>
              </div>

              <!-- Email -->
              <div style="padding:10px 14px; background:rgba(255,255,255,0.02); border-radius:8px; margin-bottom:8px; border-left:3px solid #a855f7">
                <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:6px">
                  <div style="font-size:12px; font-weight:600; color:#e2e8f0">📧 Email</div>
                  <label class="toggle-switch sm"><input type="checkbox" id="notif-ch-email" onchange="this.getRootNode().host._onNotifToggle()"><span class="toggle-slider"></span></label>
                </div>
                <div id="notif-email-wrap" style="display:none">
                  <input type="email" id="notif-email" placeholder="user@example.com" style="width:100%; padding:6px 10px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); border-radius:6px; color:#e2e8f0; font-size:11px; outline:none">
                </div>
              </div>

              <!-- Filters -->
              <div style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin:14px 0 8px">Filtry i ustawienia</div>

              <div style="display:flex; gap:12px; flex-wrap:wrap; margin-bottom:10px">
                <label style="display:flex; align-items:center; gap:5px; font-size:11px; color:#e2e8f0; cursor:pointer">
                  <input type="checkbox" id="notif-lvl-critical" checked> 🔴 Krytyczne
                </label>
                <label style="display:flex; align-items:center; gap:5px; font-size:11px; color:#e2e8f0; cursor:pointer">
                  <input type="checkbox" id="notif-lvl-warning" checked> 🟡 Ostrzeżenia
                </label>
                <label style="display:flex; align-items:center; gap:5px; font-size:11px; color:#e2e8f0; cursor:pointer">
                  <input type="checkbox" id="notif-lvl-info"> 🔵 Informacyjne
                </label>
              </div>

              <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; margin-bottom:14px">
                <div>
                  <div style="font-size:9px; color:#64748b; margin-bottom:3px">Cooldown (min)</div>
                  <input type="number" id="notif-cooldown" value="15" min="1" max="120" style="width:100%; padding:6px 10px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); border-radius:6px; color:#e2e8f0; font-size:11px; outline:none; text-align:center">
                </div>
                <div>
                  <div style="font-size:9px; color:#64748b; margin-bottom:3px">Cisza od</div>
                  <input type="time" id="notif-quiet-start" value="22:00" style="width:100%; padding:6px 10px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); border-radius:6px; color:#e2e8f0; font-size:11px; outline:none; text-align:center">
                </div>
                <div>
                  <div style="font-size:9px; color:#64748b; margin-bottom:3px">Cisza do</div>
                  <input type="time" id="notif-quiet-end" value="07:00" style="width:100%; padding:6px 10px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); border-radius:6px; color:#e2e8f0; font-size:11px; outline:none; text-align:center">
                </div>
              </div>
              <div style="font-size:9px; color:#64748b; margin-bottom:14px">⚡ Alerty krytyczne wysyłane zawsze — niezależnie od ciszy i filtrów</div>

              <!-- Actions -->
              <div style="display:flex; gap:10px">
                <button class="action-btn" style="flex:1" onclick="this.getRootNode().host._saveNotificationConfig()">💾 Zapisz konfigurację</button>
                <button class="action-btn" style="flex:1; background:rgba(0,212,255,0.1); border-color:rgba(0,212,255,0.3)" onclick="this.getRootNode().host._testNotification()">🔔 Test powiadomień</button>
              </div>
              <div id="notif-save-status" style="font-size:10px; color:#2ecc71; text-align:center; margin-top:8px; display:none"></div>

              <!-- Last sent log -->
              <div style="margin-top:14px">
                <div style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:6px">Ostatnio wysłane</div>
                <div id="notif-log-items" style="max-height:120px; overflow-y:auto; font-size:10px; color:#94a3b8">
                  <div style="text-align:center; padding:8px">Brak wysłanych powiadomień</div>
                </div>
              </div>
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

            <!-- ⚡ Energy Provider + Tariff Plan -->
            <div class="card" style="grid-column: 1 / -1">
              <div class="card-title">⚡ Dostawca energii i taryfa</div>
              <div style="font-size:11px; color:#94a3b8; margin-bottom:10px">Wybierz dostawcę energii, a następnie taryfę. Lista taryf, ceny i harmonogramy stref zostaną automatycznie dostosowane.</div>
              <div class="settings-field" style="margin-bottom:12px">
                <label style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">Dostawca energii</label>
                <select id="sel-energy-provider" style="width:100%; max-width:400px; padding:10px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:8px; color:#fff; font-size:13px" onchange="this.getRootNode().host._saveEnergyProvider()">
                  <option value="tauron">Tauron</option>
                  <option value="pge">PGE (Polska Grupa Energetyczna)</option>
                </select>
              </div>
              <div class="settings-field">
                <label style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">Plan taryfowy</label>
                <select id="sel-tariff-plan" style="width:100%; max-width:400px; padding:10px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:8px; color:#fff; font-size:13px" onchange="this.getRootNode().host._saveTariffPlan()">
                  <option value="G13">G13 — trzystrefowa (przedpołudniowa / popołudniowa / off-peak + weekendy)</option>
                  <option value="G12w">G12w — dwustrefowa + weekendy (13-15 + 22-06 + weekendy taniej)</option>
                  <option value="G12">G12 — dwustrefowa (13-15 + 22-06 taniej)</option>
                  <option value="G11">G11 — jednostrefowa (stała cena cały czas)</option>
                  <option value="Dynamic">Dynamiczna — cena godzinowa ENTSO-E</option>
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
                  • <strong>Domyślne zdjęcia:</strong> GoodWe, Deye, Growatt i Sofar Solar wczytywane automatycznie z smartinghome.pl<br>
                  • <strong>Format:</strong> PNG z przezroczystym tłem (transparent) — najlepszy efekt<br>
                  • <strong>Rozmiar:</strong> 500–800px szerokości, proporcjonalne<br>
                  • <strong>Nazwa pliku:</strong> <code style="color:#f39c12">goodwe.png</code> lub <code style="color:#f39c12">deye.png</code> lub <code style="color:#f39c12">growatt.png</code> lub <code style="color:#f39c12">sofar.png</code> (nadpisuje domyślne) lub <code style="color:#f39c12">inverter.png</code><br>
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

            <!-- 🔌 GoodWe Configuration Guide -->
            <div class="card" style="grid-column: 1 / -1" id="settings-goodwe-config">
              <div class="card-title">🔌 Konfiguracja GoodWe — configuration.yaml</div>
              <div style="font-size:11px; color:#94a3b8; margin-bottom:14px; line-height:1.5">
                Poniżej znajdziesz <strong>wymagane</strong> wpisy w <code style="background:rgba(0,212,255,0.08); padding:2px 6px; border-radius:4px; color:#00d4ff">configuration.yaml</code>.<br>
                Bez tych wpisów system HEMS i Autopilot <strong style="color:#e74c3c">nie zadziałają poprawnie</strong>.
                Kliknij sekcję aby rozwinąć, użyj przycisku 📋 aby skopiować YAML.
              </div>

              <!-- Section 1: Input Boolean -->
              <div class="gw-config-section" style="margin-bottom:8px">
                <div class="gw-config-header" onclick="this.parentElement.classList.toggle('open')" style="display:flex; align-items:center; justify-content:space-between; padding:10px 14px; background:rgba(231,76,60,0.08); border:1px solid rgba(231,76,60,0.2); border-radius:8px; cursor:pointer; transition:all 0.2s">
                  <div style="display:flex; align-items:center; gap:8px">
                    <span style="font-size:16px">🔴</span>
                    <div>
                      <div style="font-size:12px; font-weight:700; color:#e74c3c">1. Input Boolean — Przełączniki Force (WYMAGANE!)</div>
                      <div style="font-size:10px; color:#94a3b8">Bez nich: "Referenced entities ... are missing"</div>
                    </div>
                  </div>
                  <span class="gw-chevron" style="color:#64748b; transition:transform 0.2s; font-size:10px">▼</span>
                </div>
                <div class="gw-config-body" style="display:none; padding:12px 14px; border:1px solid rgba(255,255,255,0.05); border-top:none; border-radius:0 0 8px 8px; background:rgba(0,0,0,0.2)">
                  <div style="font-size:11px; color:#94a3b8; margin-bottom:8px">Dodaj w sekcji <code style="background:rgba(0,212,255,0.08); padding:1px 4px; border-radius:3px; color:#00d4ff">input_boolean:</code> pliku <code style="background:rgba(0,212,255,0.08); padding:1px 4px; border-radius:3px; color:#00d4ff">configuration.yaml</code>:</div>
                  <div style="position:relative">
                    <pre style="background:rgba(0,0,0,0.4); border:1px solid rgba(255,255,255,0.06); border-radius:6px; padding:10px 12px; font-size:10px; line-height:1.5; color:#e2e8f0; overflow-x:auto; white-space:pre" id="gw-yaml-input-boolean">input_boolean:
  hems_force_grid_charge:
    name: "Wymuś ładowanie z sieci"
    icon: mdi:battery-charging-wireless

  hems_force_battery_discharge:
    name: "Wymuś rozładowanie do sieci"
    icon: mdi:battery-arrow-down

  hems_modbus_emergency_stop:
    name: "Emergency STOP"
    icon: mdi:alert-octagon

  hems_battery_export_enabled:
    name: "Eksport baterii do sieci"
    icon: mdi:battery-arrow-up</pre>
                    <button onclick="navigator.clipboard.writeText(this.previousElementSibling.textContent); this.textContent='✅'; setTimeout(()=>this.textContent='📋',2000)" style="position:absolute; top:6px; right:6px; background:rgba(0,212,255,0.1); border:1px solid rgba(0,212,255,0.2); color:#00d4ff; padding:3px 8px; border-radius:4px; cursor:pointer; font-size:10px">📋</button>
                  </div>
                  <div style="font-size:10px; color:#f39c12; margin-top:8px">⚠️ Po dodaniu → <strong>restart Home Assistant</strong> (nie wystarczy przeładowanie konfiguracji).</div>
                </div>
              </div>

              <!-- Section 2: Modbus RS485 -->
              <div class="gw-config-section" style="margin-bottom:8px">
                <div class="gw-config-header" onclick="this.parentElement.classList.toggle('open')" style="display:flex; align-items:center; justify-content:space-between; padding:10px 14px; background:rgba(0,212,255,0.05); border:1px solid rgba(0,212,255,0.15); border-radius:8px; cursor:pointer; transition:all 0.2s">
                  <div style="display:flex; align-items:center; gap:8px">
                    <span style="font-size:16px">🔗</span>
                    <div>
                      <div style="font-size:12px; font-weight:700; color:#00d4ff">2. Modbus RS485 — połączenie z falownikiem</div>
                      <div style="font-size:10px; color:#94a3b8">Port szeregowy, slave 247, rejestry EMS/Grid/DOD</div>
                    </div>
                  </div>
                  <span class="gw-chevron" style="color:#64748b; transition:transform 0.2s; font-size:10px">▼</span>
                </div>
                <div class="gw-config-body" style="display:none; padding:12px 14px; border:1px solid rgba(255,255,255,0.05); border-top:none; border-radius:0 0 8px 8px; background:rgba(0,0,0,0.2)">
                  <div style="position:relative">
                    <pre style="background:rgba(0,0,0,0.4); border:1px solid rgba(255,255,255,0.06); border-radius:6px; padding:10px 12px; font-size:10px; line-height:1.5; color:#e2e8f0; overflow-x:auto; white-space:pre" id="gw-yaml-modbus">modbus:
  - name: goodwe_rs485
    type: serial
    method: rtu
    port: /dev/serial/by-id/usb-FTDI_...
    baudrate: 9600
    bytesize: 8
    parity: N
    stopbits: 1

    sensors:
      - name: GW Modbus EMS Mode
        slave: 247
        address: 47511
        input_type: holding
        data_type: uint16
        scan_interval: 15

      - name: GW Modbus EMS Power Limit
        slave: 247
        address: 47512
        input_type: holding
        data_type: uint16
        unit_of_measurement: W
        scan_interval: 15

      - name: GW Modbus Grid Export Enabled
        slave: 247
        address: 47509
        input_type: holding
        data_type: uint16
        scan_interval: 30

      - name: GW Modbus Grid Export Limit
        slave: 247
        address: 47510
        input_type: holding
        data_type: uint16
        unit_of_measurement: W
        scan_interval: 30

      - name: GW Modbus DOD On Grid
        slave: 247
        address: 47501
        input_type: holding
        data_type: uint16
        scan_interval: 60</pre>
                    <button onclick="navigator.clipboard.writeText(this.previousElementSibling.textContent); this.textContent='✅'; setTimeout(()=>this.textContent='📋',2000)" style="position:absolute; top:6px; right:6px; background:rgba(0,212,255,0.1); border:1px solid rgba(0,212,255,0.2); color:#00d4ff; padding:3px 8px; border-radius:4px; cursor:pointer; font-size:10px">📋</button>
                  </div>
                  <div style="font-size:10px; color:#94a3b8; margin-top:8px">⚠️ Zmień <code style="background:rgba(0,212,255,0.08); padding:1px 4px; border-radius:3px; color:#00d4ff">port:</code> na swój adapter RS485. Sprawdź: <code style="background:rgba(0,0,0,0.3); padding:1px 4px; border-radius:3px; color:#e2e8f0">ls /dev/serial/by-id/</code></div>
                </div>
              </div>

              <!-- Section 3: mletenay Integration -->
              <div class="gw-config-section" style="margin-bottom:8px">
                <div class="gw-config-header" onclick="this.parentElement.classList.toggle('open')" style="display:flex; align-items:center; justify-content:space-between; padding:10px 14px; background:rgba(46,204,113,0.05); border:1px solid rgba(46,204,113,0.15); border-radius:8px; cursor:pointer; transition:all 0.2s">
                  <div style="display:flex; align-items:center; gap:8px">
                    <span style="font-size:16px">📦</span>
                    <div>
                      <div style="font-size:12px; font-weight:700; color:#2ecc71">3. Integracja GoodWe — HACS mletenay</div>
                      <div style="font-size:10px; color:#94a3b8">WYMAGANE: mletenay experimental, NIE natywna HA</div>
                    </div>
                  </div>
                  <span class="gw-chevron" style="color:#64748b; transition:transform 0.2s; font-size:10px">▼</span>
                </div>
                <div class="gw-config-body" style="display:none; padding:12px 14px; border:1px solid rgba(255,255,255,0.05); border-top:none; border-radius:0 0 8px 8px; background:rgba(0,0,0,0.2)">
                  <div style="font-size:11px; color:#94a3b8; line-height:1.6; margin-bottom:10px">
                    <strong style="color:#fff">Instalacja:</strong><br>
                    1. HACS → Integracje → wyszukaj "GoodWe" → <strong style="color:#2ecc71">mletenay/goodwe</strong> (experimental)<br>
                    2. Zainstaluj i restart HA<br>
                    3. Ustawienia → Integracje → + → GoodWe → wpisz IP falownika
                  </div>
                  <div style="font-size:10px; font-weight:700; color:#fff; margin-bottom:6px">Wymagane encje:</div>
                  <div style="display:grid; grid-template-columns:1fr 1fr; gap:4px; font-size:9px">
                    <div style="padding:4px 8px; background:rgba(46,204,113,0.06); border-radius:4px; color:#94a3b8"><code style="color:#2ecc71">select.goodwe_tryb_pracy</code> — tryb pracy</div>
                    <div style="padding:4px 8px; background:rgba(46,204,113,0.06); border-radius:4px; color:#94a3b8"><code style="color:#2ecc71">number.goodwe_eco_mode_power</code> — moc Eco</div>
                    <div style="padding:4px 8px; background:rgba(46,204,113,0.06); border-radius:4px; color:#94a3b8"><code style="color:#2ecc71">number.goodwe_eco_mode_soc</code> — docelowy SOC</div>
                    <div style="padding:4px 8px; background:rgba(46,204,113,0.06); border-radius:4px; color:#94a3b8"><code style="color:#2ecc71">number.goodwe_dod_on_grid</code> — DOD</div>
                    <div style="padding:4px 8px; background:rgba(46,204,113,0.06); border-radius:4px; color:#94a3b8"><code style="color:#2ecc71">sensor.battery_power</code> — moc baterii</div>
                    <div style="padding:4px 8px; background:rgba(46,204,113,0.06); border-radius:4px; color:#94a3b8"><code style="color:#2ecc71">sensor.battery_state_of_charge</code> — SOC</div>
                    <div style="padding:4px 8px; background:rgba(46,204,113,0.06); border-radius:4px; color:#94a3b8"><code style="color:#2ecc71">sensor.pv_power</code> — moc PV</div>
                    <div style="padding:4px 8px; background:rgba(46,204,113,0.06); border-radius:4px; color:#94a3b8"><code style="color:#2ecc71">sensor.load</code> — zużycie domu</div>
                  </div>
                  <div style="font-size:10px; color:#f39c12; margin-top:8px">⚠️ set_parameter na BT działa TYLKO: <code style="color:#fff">battery_charge_current</code>, <code style="color:#fff">grid_export_limit</code> (jako STRING!)</div>
                </div>
              </div>

              <!-- Section 4: Template Sensor -->
              <div class="gw-config-section" style="margin-bottom:8px">
                <div class="gw-config-header" onclick="this.parentElement.classList.toggle('open')" style="display:flex; align-items:center; justify-content:space-between; padding:10px 14px; background:rgba(124,58,237,0.05); border:1px solid rgba(124,58,237,0.15); border-radius:8px; cursor:pointer; transition:all 0.2s">
                  <div style="display:flex; align-items:center; gap:8px">
                    <span style="font-size:16px">📊</span>
                    <div>
                      <div style="font-size:12px; font-weight:700; color:#7c3aed">4. Template Sensor — EMS Mode (opcjonalny)</div>
                      <div style="font-size:10px; color:#94a3b8">Dekodowanie rejestru 47511 na czytelny tekst</div>
                    </div>
                  </div>
                  <span class="gw-chevron" style="color:#64748b; transition:transform 0.2s; font-size:10px">▼</span>
                </div>
                <div class="gw-config-body" style="display:none; padding:12px 14px; border:1px solid rgba(255,255,255,0.05); border-top:none; border-radius:0 0 8px 8px; background:rgba(0,0,0,0.2)">
                  <div style="position:relative">
                    <pre style="background:rgba(0,0,0,0.4); border:1px solid rgba(255,255,255,0.06); border-radius:6px; padding:10px 12px; font-size:10px; line-height:1.5; color:#e2e8f0; overflow-x:auto; white-space:pre" id="gw-yaml-template">template:
  - sensor:
      - name: "GoodWe EMS Mode"
        unique_id: goodwe_ems_mode
        state: >
          {% set mode = states('sensor.gw_modbus_ems_mode') | int(0) %}
          {% if mode == 0 %}Auto
          {% elif mode == 1 %}Self-use
          {% elif mode == 2 %}Force charge
          {% elif mode == 4 %}Force discharge
          {% elif mode == 8 %}Hold battery
          {% else %}Nieznany ({{ mode }})
          {% endif %}</pre>
                    <button onclick="navigator.clipboard.writeText(this.previousElementSibling.textContent); this.textContent='✅'; setTimeout(()=>this.textContent='📋',2000)" style="position:absolute; top:6px; right:6px; background:rgba(0,212,255,0.1); border:1px solid rgba(0,212,255,0.2); color:#00d4ff; padding:3px 8px; border-radius:4px; cursor:pointer; font-size:10px">📋</button>
                  </div>
                </div>
              </div>

              <!-- Section 5: Verification Checklist -->
              <div class="gw-config-section" style="margin-bottom:8px">
                <div class="gw-config-header" onclick="this.parentElement.classList.toggle('open')" style="display:flex; align-items:center; justify-content:space-between; padding:10px 14px; background:rgba(247,183,49,0.05); border:1px solid rgba(247,183,49,0.15); border-radius:8px; cursor:pointer; transition:all 0.2s">
                  <div style="display:flex; align-items:center; gap:8px">
                    <span style="font-size:16px">✅</span>
                    <div>
                      <div style="font-size:12px; font-weight:700; color:#f7b731">5. Weryfikacja — Checklist po restarcie</div>
                      <div style="font-size:10px; color:#94a3b8">Sprawdź czy wszystkie encje są dostępne</div>
                    </div>
                  </div>
                  <span class="gw-chevron" style="color:#64748b; transition:transform 0.2s; font-size:10px">▼</span>
                </div>
                <div class="gw-config-body" style="display:none; padding:12px 14px; border:1px solid rgba(255,255,255,0.05); border-top:none; border-radius:0 0 8px 8px; background:rgba(0,0,0,0.2)">
                  <div style="font-size:11px; color:#94a3b8; margin-bottom:10px">W <strong style="color:#fff">Narzędzia deweloperskie → Stany</strong> sprawdź:</div>
                  <div id="gw-checklist" style="display:grid; gap:3px; font-size:10px">
                    <div style="display:flex; align-items:center; gap:6px; padding:4px 8px; background:rgba(46,204,113,0.04); border-radius:4px"><span style="color:#2ecc71">✅</span> <code style="color:#e2e8f0">input_boolean.hems_force_grid_charge</code> → off</div>
                    <div style="display:flex; align-items:center; gap:6px; padding:4px 8px; background:rgba(46,204,113,0.04); border-radius:4px"><span style="color:#2ecc71">✅</span> <code style="color:#e2e8f0">input_boolean.hems_force_battery_discharge</code> → off</div>
                    <div style="display:flex; align-items:center; gap:6px; padding:4px 8px; background:rgba(46,204,113,0.04); border-radius:4px"><span style="color:#2ecc71">✅</span> <code style="color:#e2e8f0">select.goodwe_tryb_pracy_falownika</code> → general</div>
                    <div style="display:flex; align-items:center; gap:6px; padding:4px 8px; background:rgba(46,204,113,0.04); border-radius:4px"><span style="color:#2ecc71">✅</span> <code style="color:#e2e8f0">sensor.gw_modbus_ems_mode</code> → 0 lub 1</div>
                    <div style="display:flex; align-items:center; gap:6px; padding:4px 8px; background:rgba(46,204,113,0.04); border-radius:4px"><span style="color:#2ecc71">✅</span> <code style="color:#e2e8f0">sensor.battery_state_of_charge</code> → 0–100%</div>
                    <div style="display:flex; align-items:center; gap:6px; padding:4px 8px; background:rgba(46,204,113,0.04); border-radius:4px"><span style="color:#2ecc71">✅</span> <code style="color:#e2e8f0">sensor.battery_power</code> → wartość W</div>
                    <div style="display:flex; align-items:center; gap:6px; padding:4px 8px; background:rgba(46,204,113,0.04); border-radius:4px"><span style="color:#2ecc71">✅</span> <code style="color:#e2e8f0">sensor.meter_active_power_total</code> → wartość W</div>
                  </div>
                  <div style="font-size:10px; color:#94a3b8; margin-top:10px; line-height:1.5">
                    <strong style="color:#e74c3c">Jeśli unavailable:</strong><br>
                    • <code style="color:#fff">input_boolean.*</code> → nie dodano do configuration.yaml<br>
                    • <code style="color:#fff">sensor.gw_modbus_*</code> → problem z Modbus RS485 (kabel/port/slave)<br>
                    • <code style="color:#fff">select/number.goodwe_*</code> → integracja mletenay nie działa<br>
                    • <code style="color:#fff">sensor.battery_*</code> → mletenay nie połączony z falownikiem
                  </div>
                </div>
              </div>

              <!-- CSS for accordion -->
              <style>
                .gw-config-section.open .gw-config-body { display:block !important; }
                .gw-config-section.open .gw-chevron { transform:rotate(180deg); }
                .gw-config-header:hover { filter:brightness(1.15); }
              </style>
            </div>

            <!-- ℹ️ Info -->
            <div class="card" style="grid-column: 1 / -1">
              <div class="card-title">ℹ️ Informacje</div>
              <div class="dr"><span class="lb">Wersja integracji</span><span class="vl">1.53.4</span></div>
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
    // Connection watchdog — detect stale hass connection every 30s (soft recovery, no DOM rebuild)
    if (this._watchdog) clearInterval(this._watchdog);
    this._watchdog = setInterval(() => {
      if (!this._hass?.connection) return;
      try {
        this._hass.connection.ping().catch(() => {
          console.warn('[SH] Connection stale — soft recovery (no render)');
          this._ensureSubscriptions();
          if (this._hass) this._updateAll();
        });
      } catch(e) {
        console.warn('[SH] Watchdog ping error:', e);
      }
    }, 30000);
  }

  /* ═══════════════════════════════════════════════════
     ALERTS TAB — Tier Visibility
     ═══════════════════════════════════════════════════ */

  _updateAlertsVisibility() {
    const btn = this.shadowRoot.getElementById('tab-btn-alerts');
    if (!btn) return;
    const tier = this._tier();
    const show = (tier === 'PRO' || tier === 'ENTERPRISE');
    btn.style.display = show ? 'inline-block' : 'none';
    if (show && !this._alertsTabLogged) {
      console.log('[SH] Alerts tab visible (tier=' + tier + ')');
      this._alertsTabLogged = true;
    }
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

  /* ── Peak Sell — slider handlers ── */
  _onPeakSellSliderChange(value) {
    const v = parseInt(value);
    const badge = this.shadowRoot.getElementById('ap-peak-sell-badge');
    if (badge) {
      badge.textContent = v === 0 ? 'WYŁĄCZONE' : `${v}%`;
      badge.style.color = v === 0 ? '#64748b' : '#f7b731';
      badge.style.background = v === 0 ? 'rgba(100,116,139,0.15)' : 'rgba(247,183,49,0.15)';
    }
    this._updatePeakSellDisplay(v);
  }

  async _savePeakSellPercent(value) {
    const v = parseInt(value);
    // Save to settings.json
    this._savePanelSettings({ peak_sell_soc_percent: v });
    // Call backend service to update strategy controller
    if (this._hass) {
      try {
        await this._hass.callService('smartinghome', 'set_peak_sell_percent', {
          percent: v,
        });
        console.log('[SH] Peak sell set to', v, '%');
      } catch (err) {
        console.warn('[SH] Peak sell service call failed (will use settings.json fallback):', err.message || err);
      }
    }
  }

  _updatePeakSellDisplay(sellPercent) {
    if (sellPercent === undefined) {
      const slider = this.shadowRoot.getElementById('ap-peak-sell-slider');
      sellPercent = slider ? parseInt(slider.value) : 50;
    }
    // Get current SOC and battery capacity
    const soc = this._nm('battery_soc') || 0;
    const batCapWh = parseFloat(this._settings?.battery_capacity_kwh || 10.2) * 1000 || 10200;
    const batCapKwh = batCapWh / 1000;

    // Calculate sell target SOC (with floor of 20%)
    const sellTarget = Math.max(soc - sellPercent, 20);
    const sellKwh = Math.max(0, (soc - sellTarget) / 100 * batCapKwh);
    const reserveKwh = Math.max(0, (sellTarget - 5) / 100 * batCapKwh); // 5% = hardware min

    // Revenue estimate — use current RCE
    const rceState = this._hass?.states['sensor.smartinghome_rce_price'];
    const rceMwh = rceState ? parseFloat(rceState.state) || 500 : 500;
    const rceSellKwh = rceMwh / 1000 * 1.23; // prosumer coefficient
    const revenue = sellKwh * rceSellKwh;

    const elKwh = this.shadowRoot.getElementById('ap-peak-sell-kwh');
    const elReserve = this.shadowRoot.getElementById('ap-peak-sell-reserve');
    const elRevenue = this.shadowRoot.getElementById('ap-peak-sell-revenue');

    if (elKwh) elKwh.textContent = sellPercent === 0 ? '—' : `${sellKwh.toFixed(1)} kWh`;
    if (elReserve) elReserve.textContent = sellPercent === 0 ? `${(soc > 5 ? (soc - 5) / 100 * batCapKwh : 0).toFixed(1)} kWh` : `${reserveKwh.toFixed(1)} kWh`;
    if (elRevenue) elRevenue.textContent = sellPercent === 0 ? '—' : `~${revenue.toFixed(2)} PLN`;
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

          // ── Update Overview tab Autopilot banner ──
          const ovBanner = this.shadowRoot.getElementById('ov-autopilot-banner');
          if (ovBanner) {
            if (saved) {
              ovBanner.style.display = 'block';
              const ovStrategyLabels = {
                max_self_consumption: '🟢 Max Autokonsumpcja',
                max_profit: '💰 Max Zysk',
                battery_protection: '🔋 Ochrona Baterii',
                zero_export: '⚡ Zero Export',
                weather_adaptive: '🌧️ Pogodowy',
                ai_full_autonomy: '🧠 AI Pełna Autonomia',
              };
              const ovStrat = this.shadowRoot.getElementById('ov-ap-strategy');
              if (ovStrat) ovStrat.textContent = ovStrategyLabels[saved] || saved;
            } else {
              ovBanner.style.display = 'none';
            }
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

            // Update overview banner zone
            const ovZone = this.shadowRoot.getElementById('ov-ap-zone');
            if (ovZone) {
              const zoneMap2 = { off_peak: '🌙 Off-peak', morning_peak: '☀️ Szczyt poranny', afternoon_peak: '⚡ Szczyt popołudniowy' };
              ovZone.textContent = zoneMap2[live.g13_zone] || '';
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

  /* ═══════════════════════════════════════════════ */
  /* ═══  FORECAST TAB — Prognoza Solarna AI  ═══ */
  /* ═══════════════════════════════════════════════ */

  async _initForecastTab() {
    // Restore strategy from settings
    const mode = this._settings.forecast_strategy || 'AUTARKIA';
    this._setForecastStrategy(mode, true);

    // Render PV config cards (from pv_string_config or manual fallback)
    this._renderForecastPvConfig();

    // Fetch solar forecast
    try {
      await this._fetchSolarForecast();
    } catch (e) {
      console.warn('[SH] Forecast tab init error:', e);
    }

    // Update integration status
    this._updateIntegrationStatus();

    // Load calibration data
    this._updateCalibration();
  }

  async _fetchSolarForecast() {
    if (!this._hass) return;

    // Get location from zone.home
    const zone = this._hass.states['zone.home'];
    const lat = zone?.attributes?.latitude;
    const lon = zone?.attributes?.longitude;
    if (!lat || !lon) {
      console.warn('[SH] Forecast: No zone.home lat/lon found');
      this._setText('fc-kpi-today-sub', '⚠️ Brak lokalizacji (zone.home)');
      return;
    }

    // Build PV string array — each entry has { wp, tilt, azimuth, label }
    // Priority 1: pv_string_config (most precise — per-string tilt/azimuth)
    const dirMap = { 'N': 0, 'NE': 45, 'E': 90, 'SE': 135, 'S': 180, 'SW': 225, 'W': 270, 'NW': 315 };
    let pvStrings = [];
    const cfg = this._settings.pv_string_config || {};
    for (let i = 1; i <= 4; i++) {
      const sc = cfg[`pv${i}`];
      if (sc && sc.substrings) {
        sc.substrings.forEach((sub, si) => {
          const wp = (sub.panel_count || 0) * (sub.panel_power || 405);
          if (wp > 0) {
            pvStrings.push({
              wp,
              tilt: sub.tilt || 35,
              azimuth: dirMap[sub.direction] || 180,
              label: sc.name || `String ${i}${sc.substrings.length > 1 ? `.${si+1}` : ''}`
            });
          }
        });
      }
    }

    // Priority 2: forecast tab manual config
    if (pvStrings.length === 0) {
      const fcKwp = parseFloat(this._settings.forecast_pv_kwp) || 0;
      if (fcKwp > 0) {
        pvStrings.push({
          wp: fcKwp * 1000,
          tilt: parseFloat(this._settings.forecast_pv_tilt) || 35,
          azimuth: parseFloat(this._settings.forecast_pv_azimuth) || 180,
          label: 'Ręczna konfiguracja'
        });
      }
    }

    // Priority 3: Zima na plusie kWp (no tilt/az data)
    if (pvStrings.length === 0) {
      const winterKwp = parseFloat(this._settings.winter_pv_kwp) || 0;
      if (winterKwp > 0) {
        pvStrings.push({ wp: winterKwp * 1000, tilt: 35, azimuth: 180, label: 'Zima na plusie' });
      }
    }

    // Priority 4: Default
    if (pvStrings.length === 0) {
      pvStrings.push({ wp: 5000, tilt: 35, azimuth: 180, label: 'Domyślne 5 kWp' });
    }

    const totalWp = pvStrings.reduce((s, p) => s + p.wp, 0);

    // Update config UI — refresh string cards and manual fallback fields
    this._renderForecastPvConfig();
    const kwpInput = this.shadowRoot.getElementById('fc-cfg-kwp');
    if (kwpInput) kwpInput.value = (totalWp / 1000).toFixed(1);
    const wTilt = pvStrings.reduce((s, p) => s + p.tilt * p.wp, 0) / totalWp;
    const wAz = pvStrings.reduce((s, p) => s + p.azimuth * p.wp, 0) / totalWp;
    const tiltInput = this.shadowRoot.getElementById('fc-cfg-tilt');
    if (tiltInput) tiltInput.value = Math.round(wTilt);
    const azSelect = this.shadowRoot.getElementById('fc-cfg-azimuth');
    if (azSelect) azSelect.value = String(Math.round(wAz / 45) * 45);
    this._updateForecastCfgSummary(totalWp / 1000, pvStrings);

    // Fetch Open-Meteo Solar Radiation
    const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&hourly=shortwave_radiation,temperature_2m,cloud_cover,direct_radiation,diffuse_radiation&daily=shortwave_radiation_sum&timezone=auto&forecast_days=7`;

    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`Open-Meteo HTTP ${resp.status}`);
    const data = await resp.json();

    this._setText('fc-integ-meteo', '✅ Połączono');
    const meteoEl = this.shadowRoot.getElementById('fc-integ-meteo');
    if (meteoEl) meteoEl.style.color = '#2ecc71';

    // Process hourly data for today — calculate per-string and sum
    const now = new Date();
    const todayStr = now.toISOString().slice(0, 10);
    const hourlyData = [];
    const hourlyTimes = data.hourly?.time || [];
    const hourlyGHI = data.hourly?.shortwave_radiation || [];
    const hourlyTemp = data.hourly?.temperature_2m || [];
    const hourlyCloud = data.hourly?.cloud_cover || [];

    let todayTotalKwh = 0;
    let peakKw = 0;
    let peakHourStart = 0;
    let peakHourEnd = 0;
    const cf = this._settings.forecast_calibration_factor || 1.0;

    for (let i = 0; i < hourlyTimes.length; i++) {
      const t = hourlyTimes[i];
      if (!t.startsWith(todayStr)) continue;
      const hour = parseInt(t.slice(11, 13));
      const ghi = hourlyGHI[i] || 0;
      const temp = hourlyTemp[i];
      const cloud = hourlyCloud[i];
      // Sum production across all strings (each with own tilt/azimuth)
      let kw = 0;
      pvStrings.forEach(ps => {
        kw += this._calcPVProduction(ghi, temp, ps.wp, ps.tilt, ps.azimuth);
      });
      kw *= cf;
      todayTotalKwh += kw;

      hourlyData.push({ hour, kw, temp, cloud, ghi });

      if (kw > peakKw) { peakKw = kw; peakHourStart = hour; }
    }

    // Determine peak window (hours with > 70% of peak)
    const peakThreshold = peakKw * 0.7;
    const peakHours = hourlyData.filter(h => h.kw >= peakThreshold).map(h => h.hour);
    if (peakHours.length > 0) {
      peakHourStart = Math.min(...peakHours);
      peakHourEnd = Math.max(...peakHours);
    }

    // Store for decision engine
    this._fcHourlyData = hourlyData;
    this._fcTodayKwh = todayTotalKwh;
    this._fcPeakWindow = { start: peakHourStart, end: peakHourEnd, kw: peakKw };
    this._fcTotalWp = totalWp;
    this._fcPvStrings = pvStrings;

    // Process daily data — per-string calculation
    const dailyData = [];
    const dailyTimes = data.daily?.time || [];
    const dailyRad = data.daily?.shortwave_radiation_sum || [];
    const dayNames = ['NIEDZ.', 'PON.', 'WT.', 'ŚR.', 'CZW.', 'PT.', 'SOB.'];

    for (let i = 0; i < dailyTimes.length && i < 7; i++) {
      const d = new Date(dailyTimes[i]);
      const radSum = dailyRad[i] || 0;
      const avgGhi = (radSum * 1e6) / (3600 * 12);
      let kwh = 0;
      pvStrings.forEach(ps => {
        kwh += this._calcPVProduction(avgGhi, 15, ps.wp, ps.tilt, ps.azimuth);
      });
      kwh *= 12 * cf;
      dailyData.push({
        date: dailyTimes[i],
        dayName: dayNames[d.getDay()],
        dayNum: `${d.getDate()}.${d.getMonth() + 1}`,
        kwh: kwh,
        isToday: dailyTimes[i] === todayStr
      });
    }
    this._fcDailyData = dailyData;

    // Render all sections
    this._updateForecastKPI(todayTotalKwh, peakHourStart, peakHourEnd, peakKw);
    this._renderWeeklyChart(dailyData);
    this._renderHourlyCurve(hourlyData);
    this._renderHourlyList(hourlyData);
    this._generateDecisions();
    this._updateCalibrationFromProduction(todayTotalKwh);
  }

  _calcPVProduction(ghi, temp, totalWp, tilt, azimuth) {
    // Physics-based PV production model
    // P(kW) = (GHI / STC_irradiance) × Prated(kW) × η_system × corrections
    if (ghi <= 0) return 0;
    const radians = Math.PI / 180;
    const tiltFactor = Math.cos((tilt - 35) * radians * 0.5); // optimal ~35° in Poland
    const azFactor = 1 - Math.abs(azimuth - 180) / 360 * 0.3; // South=180 optimal
    // Temperature correction: -0.4%/°C above 25°C (STC)
    const tempCoeff = 1 - Math.max(0, (temp || 25) - 25) * 0.004;
    // System efficiency: inverter + wiring + soiling + mismatch ~ 0.82
    const eta = 0.82;
    // Prated is already in Wp, GHI=1000 W/m² → Prated output at STC
    const pKw = (ghi / 1000) * (totalWp / 1000) * eta * tiltFactor * azFactor * tempCoeff;
    return Math.max(0, pKw);
  }

  _updateForecastKPI(todayKwh, peakStart, peakEnd, peakKw) {
    const el = this.shadowRoot.getElementById('fc-kpi-today');
    if (el) el.innerHTML = `${todayKwh.toFixed(1)} <span style="font-size:14px; font-weight:400">kWh</span>`;

    const realPv = this._nm('pv_today') || 0;
    const sub = this.shadowRoot.getElementById('fc-kpi-today-sub');
    if (sub) sub.textContent = realPv > 0 ? `Rzeczywista: ${realPv.toFixed(1)} kWh` : 'prognoza Open-Meteo';

    const peak = this.shadowRoot.getElementById('fc-kpi-peak');
    if (peak) peak.textContent = `${String(peakStart).padStart(2,'0')}:00–${String(peakEnd + 1).padStart(2,'0')}:00`;
    const peakSub = this.shadowRoot.getElementById('fc-kpi-peak-sub');
    if (peakSub) peakSub.textContent = `max ${peakKw.toFixed(1)} kW`;

    const mode = this._settings.forecast_strategy || 'AUTARKIA';
    this._setText('fc-kpi-strategy', mode.replace('_', ' '));
  }

  _renderWeeklyChart(dailyData) {
    const container = this.shadowRoot.getElementById('fc-week-bars');
    if (!container || !dailyData.length) return;
    const maxKwh = Math.max(...dailyData.map(d => d.kwh), 1);
    const totalKwh = dailyData.reduce((s, d) => s + d.kwh, 0);

    this._setText('fc-week-total', totalKwh.toFixed(1));
    this._setText('fc-week-avg', (totalKwh / dailyData.length).toFixed(1));

    container.innerHTML = dailyData.map(d => {
      const barMaxH = 140; // px available for bars
      const barH = Math.max(6, (d.kwh / maxKwh) * barMaxH);
      const color = d.kwh > 20 ? '#2ecc71' : d.kwh > 10 ? '#00d4ff' : '#475569';
      const todayMark = d.isToday ? 'border:2px solid #f7b731;' : '';
      const valColor = d.isToday ? '#f7b731' : '#fff';
      return `<div class="fc-week-col">
        <div class="fc-week-val" style="color:${valColor}">${d.kwh.toFixed(1)}</div>
        <div class="fc-week-bar" style="height:${barH.toFixed(0)}px; background:${color}; ${todayMark}"></div>
        <div class="fc-week-day" style="${d.isToday ? 'color:#f7b731; font-weight:800' : ''}">${d.isToday ? 'DZIŚ' : d.dayName}</div>
        <div class="fc-week-date">${d.dayNum}</div>
      </div>`;
    }).join('');
  }

  _renderHourlyCurve(hourlyData) {
    const el = this.shadowRoot.getElementById('fc-hourly-curve');
    if (!el || !hourlyData.length) return;
    const nowHour = new Date().getHours() + new Date().getMinutes() / 60;
    const sunHours = hourlyData.filter(h => h.hour >= 5 && h.hour <= 20);
    if (sunHours.length === 0) { el.innerHTML = ''; return; }
    const maxKw = Math.max(...sunHours.map(h => h.kw), 0.1);
    const W = 800, H = 200, padX = 40, padY = 20;
    const chartW = W - padX * 2, chartH = H - padY * 2;

    // Build forecast curve points
    const pts = sunHours.map(h => {
      const x = padX + ((h.hour - 5) / 15) * chartW;
      const y = padY + chartH - (h.kw / maxKw) * chartH;
      return { x, y, h };
    });

    // Smooth path
    let path = `M ${pts[0].x} ${pts[0].y}`;
    for (let i = 1; i < pts.length; i++) {
      const cx = (pts[i - 1].x + pts[i].x) / 2;
      path += ` C ${cx} ${pts[i - 1].y}, ${cx} ${pts[i].y}, ${pts[i].x} ${pts[i].y}`;
    }
    const areaPath = path + ` L ${pts[pts.length - 1].x} ${padY + chartH} L ${pts[0].x} ${padY + chartH} Z`;

    // Now marker
    const nowX = padX + ((nowHour - 5) / 15) * chartW;
    const realPv = (this._nm('pv_power') || 0) / 1000;
    const realY = padY + chartH - (Math.min(realPv, maxKw) / maxKw) * chartH;

    // Grid lines + labels
    let gridLines = '';
    for (let h = 6; h <= 20; h += 2) {
      const x = padX + ((h - 5) / 15) * chartW;
      gridLines += `<line x1="${x}" y1="${padY}" x2="${x}" y2="${padY + chartH}" stroke="rgba(255,255,255,0.04)" stroke-width="0.5"/>`;
      gridLines += `<text x="${x}" y="${H - 2}" text-anchor="middle" fill="#475569" font-size="9">${h}:00</text>`;
    }
    for (let v = 0; v <= maxKw; v += Math.max(0.5, Math.ceil(maxKw / 4))) {
      const y = padY + chartH - (v / maxKw) * chartH;
      gridLines += `<line x1="${padX}" y1="${y}" x2="${W - padX}" y2="${y}" stroke="rgba(255,255,255,0.04)" stroke-width="0.5"/>`;
      gridLines += `<text x="${padX - 4}" y="${y + 3}" text-anchor="end" fill="#475569" font-size="8">${v.toFixed(1)}</text>`;
    }

      // Ecowitt real-time solar radiation overlay
      let ecowittOverlay = '';
      if (this._settings.ecowitt_enabled && nowHour >= 5 && nowHour <= 20) {
        const ecoRad = parseFloat(this._hass?.states?.['sensor.ecowitt_solar_radiation_9747']?.state);
        if (!isNaN(ecoRad) && ecoRad > 0) {
          // Convert W/m² to approximate kW using rated power
          const ecoKw = (ecoRad / 1000) * ((this._fcTotalWp || 5000) / 1000) * 0.82;
          const ecoY = padY + chartH - (Math.min(ecoKw, maxKw) / maxKw) * chartH;
          ecowittOverlay = `
            <circle cx="${nowX}" cy="${ecoY}" r="4" fill="none" stroke="#00d4ff" stroke-width="1.5" style="filter:drop-shadow(0 0 4px #00d4ff)"/>
            <text x="${nowX + 12}" y="${ecoY + 3}" fill="#00d4ff" font-size="8">Ecowitt ${ecoRad.toFixed(0)} W/m²</text>
          `;
        }
      }

      el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="fc-curve-svg" style="height:200px">
        <defs>
          <linearGradient id="fc-grad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#2ecc71" stop-opacity="0.25"/>
            <stop offset="100%" stop-color="#2ecc71" stop-opacity="0.02"/>
          </linearGradient>
        </defs>
        ${gridLines}
        <path d="${areaPath}" fill="url(#fc-grad)"/>
        <path d="${path}" fill="none" stroke="#2ecc71" stroke-width="2.5" stroke-linecap="round"/>
        ${nowHour >= 5 && nowHour <= 20 ? `
          <line x1="${nowX}" y1="${padY}" x2="${nowX}" y2="${padY + chartH}" class="fc-now-line"/>
          <circle cx="${nowX}" cy="${realY}" r="5" fill="#f7b731" style="filter:drop-shadow(0 0 6px #f7b731)"/>
          <text x="${nowX}" y="${realY - 10}" text-anchor="middle" fill="#f7b731" font-size="10" font-weight="700">${realPv.toFixed(1)} kW</text>
          <text x="${nowX}" y="${padY - 4}" text-anchor="middle" fill="#f7b731" font-size="8">teraz</text>
          ${ecowittOverlay}
        ` : ''}
      </svg>`;
  }

  _renderHourlyList(hourlyData) {
    const el = this.shadowRoot.getElementById('fc-hourly-list');
    if (!el) return;
    const maxKw = Math.max(...hourlyData.map(h => h.kw), 0.1);
    const nowHour = new Date().getHours();

    el.innerHTML = hourlyData.filter(h => h.hour >= 5 && h.hour <= 20).map(h => {
      const pct = (h.kw / maxKw * 100).toFixed(0);
      const color = h.kw > maxKw * 0.7 ? '#2ecc71' : h.kw > maxKw * 0.3 ? '#f39c12' : '#475569';
      const isNow = h.hour === nowHour;
      const bg = isNow ? 'background:rgba(247,183,49,0.08); border-left:3px solid #f7b731;' : '';
      return `<div class="fc-hourly-row" style="${bg}">
        <span class="fc-hourly-hour" style="${isNow ? 'color:#f7b731' : ''}">${String(h.hour).padStart(2,'0')}:00</span>
        <div class="fc-hourly-bar-wrap">
          <div class="fc-hourly-bar" style="width:${pct}%; background:${color}"></div>
        </div>
        <span class="fc-hourly-kw" style="color:${color}">${h.kw.toFixed(2)}</span>
        <span class="fc-hourly-temp">${h.temp != null ? h.temp.toFixed(0) + '°' : '—'}</span>
      </div>`;
    }).join('');
  }

  _generateDecisions() {
    const el = this.shadowRoot.getElementById('fc-decisions');
    if (!el) return;
    const mode = this._settings.forecast_strategy || 'AUTARKIA';
    const soc = this._nm('battery_soc') || 0;
    const peak = this._fcPeakWindow || { start: 11, end: 14, kw: 3 };
    const todayKwh = this._fcTodayKwh || 0;
    const nowHour = new Date().getHours();
    const price = parseFloat(this._hass?.states?.['sensor.entso_e_koszt_all_in_teraz']?.state) || 0;
    const avgPrice = parseFloat(this._hass?.states?.['sensor.entso_e_srednia_dzisiaj']?.state) || 0.50;
    const cards = [];

    // Decision: Battery charge
    if (soc < 50 && todayKwh > 10) {
      cards.push({ icon: '🔋', title: `Ładuj baterię ${peak.start}:00–${peak.end}:00`, desc: `SOC=${soc.toFixed(0)}% — wykorzystaj szczyt PV (${peak.kw.toFixed(1)} kW) do naładowania`, badge: 'AUTOMAT', badgeClass: 'fc-badge-auto' });
    } else if (soc > 80) {
      cards.push({ icon: '✅', title: 'Bateria naładowana', desc: `SOC=${soc.toFixed(0)}% — gotowa do wieczornego użycia`, badge: 'OK', badgeClass: 'fc-badge-auto' });
    }

    // Decision: Grid export
    if (price > avgPrice * 1.2 && nowHour >= 8 && nowHour <= 16) {
      cards.push({ icon: '💰', title: `Sprzedawaj energię TERAZ`, desc: `Cena ${price.toFixed(2)} zł/kWh > średnia ${avgPrice.toFixed(2)} — opłacalny eksport`, badge: 'MAX ZYSK', badgeClass: 'fc-badge-zysk' });
    } else if (mode === 'MAX_ZYSK' && peak.kw > 2) {
      cards.push({ icon: '⚡', title: `Eksportuj ${peak.start}:00–${peak.end + 1}:00`, desc: `Peak ${peak.kw.toFixed(1)} kW — najlepsze godziny sprzedaży`, badge: 'MAX ZYSK', badgeClass: 'fc-badge-zysk' });
    }

    // Decision: Heavy devices
    if (todayKwh > 15 && peak.kw > 2) {
      cards.push({ icon: '🔌', title: `Uruchom energochłonne urządzenia ${peak.start}:00–${peak.end}:00`, desc: 'Pranie, zmywarka, pompę ciepła — najwyższa produkcja PV', badge: 'SMART LOAD', badgeClass: 'fc-badge-load' });
    }

    // Decision: Low production warning
    if (todayKwh < 5) {
      cards.push({ icon: '⚠️', title: 'Słaby dzień solarny', desc: `Prognoza: ${todayKwh.toFixed(1)} kWh — ogranicz zużycie lub kup taniej w nocy`, badge: 'UWAGA', badgeClass: 'fc-badge-load' });
    }

    // Decision: Strategy-specific
    if (mode === 'AUTARKIA' && soc < 30 && price < avgPrice * 0.8) {
      cards.push({ icon: '🌙', title: 'Ładuj baterię z sieci (niska cena)', desc: `Cena ${price.toFixed(2)} zł — poniżej średniej, warto doładować na wieczór`, badge: 'AUTARKIA', badgeClass: 'fc-badge-auto' });
    }

    if (cards.length === 0) {
      cards.push({ icon: '✅', title: 'System pracuje optymalnie', desc: 'Brak specjalnych rekomendacji — Autopilot zarządza energią automatycznie', badge: 'OK', badgeClass: 'fc-badge-auto' });
    }

    el.innerHTML = cards.map(c => `<div class="fc-decision">
      <div class="fc-decision-icon">${c.icon}</div>
      <div class="fc-decision-body">
        <div class="fc-decision-title">${c.title}</div>
        <div class="fc-decision-desc">${c.desc}</div>
      </div>
      <span class="fc-decision-badge ${c.badgeClass}">${c.badge}</span>
    </div>`).join('');
  }

  _setForecastStrategy(mode, noSave) {
    ['zysk', 'autarkia', 'eco'].forEach(k => {
      const btn = this.shadowRoot.getElementById(`fc-str-${k}`);
      if (btn) btn.classList.toggle('active', mode === { zysk: 'MAX_ZYSK', autarkia: 'AUTARKIA', eco: 'ECO' }[k]);
    });
    this._setText('fc-kpi-strategy', mode.replace('_', ' '));
    if (!noSave) {
      this._savePanelSettings({ forecast_strategy: mode });
      // Regenerate decisions with new mode
      if (this._fcHourlyData) this._generateDecisions();
    }
  }

  _saveForecastConfig(recompute) {
    // If pv_string_config is active, skip saving manual fallback values — just recompute
    const cfg = this._settings.pv_string_config || {};
    const hasStringConfig = Object.keys(cfg).some(k => {
      const sc = cfg[k];
      return sc && sc.substrings && sc.substrings.some(sub => (sub.panel_count || 0) * (sub.panel_power || 0) > 0);
    });

    if (!hasStringConfig) {
      const kwp = parseFloat(this.shadowRoot.getElementById('fc-cfg-kwp')?.value) || 9;
      const tilt = parseInt(this.shadowRoot.getElementById('fc-cfg-tilt')?.value) || 35;
      const azimuth = parseInt(this.shadowRoot.getElementById('fc-cfg-azimuth')?.value) || 180;
      this._savePanelSettings({
        forecast_pv_kwp: kwp,
        forecast_pv_tilt: tilt,
        forecast_pv_azimuth: azimuth,
        // Sync kWp with Zima na plusie for consistency
        winter_pv_kwp: kwp,
      });
      this._updateForecastCfgSummary(kwp);
    }
    if (recompute) {
      // Refresh forecast with new/existing params
      this._fetchSolarForecast().catch(e => console.warn('[SH] recompute error:', e));
    }
  }

  _updateForecastCfgSummary(kwp, pvStrings) {
    const el = this.shadowRoot.getElementById('fc-cfg-summary');
    if (!el) return;
    const zone = this._hass?.states['zone.home'];
    const lat = zone?.attributes?.latitude || 51;
    const kwhPerKwp = lat > 52 ? 950 : lat > 50 ? 1000 : 1050;
    const annual = (kwp * kwhPerKwp).toFixed(0);
    const azLabels = { 0:'N', 45:'NE', 90:'E', 135:'SE', 180:'S', 225:'SW', 270:'W', 315:'NW' };
    let html = `Roczna produkcja: <strong style="color:#2ecc71">${annual} kWh</strong> (~${kwhPerKwp} kWh/kWp)`;
    if (pvStrings && pvStrings.length > 1) {
      html += '<br><span style="font-size:9px; color:#64748b">';
      html += pvStrings.map(ps => {
        const dir = azLabels[ps.azimuth] || `${ps.azimuth}°`;
        return `${ps.label}: ${(ps.wp/1000).toFixed(1)} kWp ${dir} ${ps.tilt}°`;
      }).join(' · ');
      html += '</span>';
    } else if (pvStrings && pvStrings.length === 1 && pvStrings[0].label !== 'Domyślne 5 kWp') {
      const ps = pvStrings[0];
      const dir = azLabels[ps.azimuth] || `${ps.azimuth}°`;
      html += ` <span style="font-size:9px; color:#64748b">· ${ps.label}: ${dir} ${ps.tilt}°</span>`;
    }
    el.innerHTML = html;
  }

  _renderForecastPvConfig() {
    const container = this.shadowRoot.getElementById('fc-pv-config-area');
    if (!container) return;

    const cfg = this._settings.pv_string_config || {};
    const pvLabels = this._settings.pv_labels || {};
    const dirLabels = { N:'Północ', NE:'Pn-Wsch', E:'Wschód', SE:'Pd-Wsch', S:'Południe', SW:'Pd-Zach', W:'Zachód', NW:'Pn-Zach' };
    const dirIcons = { N:'⬆️', NE:'↗️', E:'🌅', SE:'↘️', S:'☀️', SW:'↙️', W:'🌇', NW:'↖️' };
    const dirMap = { N:0, NE:45, E:90, SE:135, S:180, SW:225, W:270, NW:315 };

    // Collect all configured strings/substrings
    let allStrings = [];
    for (let i = 1; i <= 4; i++) {
      const sc = cfg[`pv${i}`];
      if (sc && sc.substrings) {
        sc.substrings.forEach((sub, si) => {
          const wp = (sub.panel_count || 0) * (sub.panel_power || 405);
          if (wp > 0) {
            allStrings.push({
              stringIdx: i, subIdx: si,
              label: pvLabels[`pv${i}`] || `PV${i}`,
              hasSubstrings: sc.has_substrings && sc.substrings.length > 1,
              subLabel: (sc.has_substrings && sc.substrings.length > 1) ? `Podstring ${si + 1}` : null,
              wp, direction: sub.direction || 'S',
              tilt: sub.tilt || 35,
              panelCount: sub.panel_count || 0,
              panelPower: sub.panel_power || 405
            });
          }
        });
      }
    }

    const subtitle = this.shadowRoot.getElementById('fc-pv-config-subtitle');

    if (allStrings.length > 0) {
      // === MULTI-STRING MODE: show cards ===
      if (subtitle) subtitle.textContent = 'Konfiguracja pobrana z Przeglądu → Produkcja PV';
      const totalWp = allStrings.reduce((s, p) => s + p.wp, 0);

      const cardsHtml = allStrings.map(s => {
        const dir = s.direction || 'S';
        const icon = dirIcons[dir] || '☀️';
        const dirLabel = dirLabels[dir] || dir;
        const kwp = (s.wp / 1000).toFixed(2);
        const displayLabel = s.subLabel ? `${s.label} — ${s.subLabel}` : s.label;
        const pct = totalWp > 0 ? ((s.wp / totalWp) * 100).toFixed(0) : 0;

        return `<div style="background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08); border-radius:10px; padding:12px; display:flex; flex-direction:column; gap:6px; position:relative; transition:border-color 0.3s, background 0.3s; cursor:default"
          onmouseenter="this.style.borderColor='rgba(247,183,49,0.3)'; this.style.background='rgba(255,255,255,0.06)'"
          onmouseleave="this.style.borderColor='rgba(255,255,255,0.08)'; this.style.background='rgba(255,255,255,0.04)'">
          <div style="display:flex; align-items:center; justify-content:space-between">
            <div style="display:flex; align-items:center; gap:6px">
              <span style="font-size:18px">${icon}</span>
              <span style="font-size:11px; font-weight:700; color:#f8fafc">${displayLabel}</span>
            </div>
            <span style="font-size:10px; color:#f7b731; cursor:pointer; padding:2px 6px; border-radius:4px; background:rgba(247,183,49,0.1); transition:background 0.2s"
              onmouseenter="this.style.background='rgba(247,183,49,0.2)'"
              onmouseleave="this.style.background='rgba(247,183,49,0.1)'"
              onclick="this.getRootNode().host._openPvStringConfig(${s.stringIdx})"
              title="Edytuj parametry stringa">⚙️ Edytuj</span>
          </div>
          <div style="display:flex; align-items:baseline; gap:4px">
            <span style="font-size:20px; font-weight:800; color:#2ecc71">${kwp}</span>
            <span style="font-size:11px; color:#94a3b8">kWp</span>
            <span style="font-size:10px; color:#475569; margin-left:auto">${pct}%</span>
          </div>
          <div style="display:flex; gap:8px; font-size:10px; color:#64748b">
            <span>📐 ${s.tilt}°</span>
            <span>🧭 ${dirLabel}</span>
          </div>
          <div style="font-size:9px; color:#475569">${s.panelCount} × ${s.panelPower} Wp</div>
        </div>`;
      }).join('');

      // Determine grid columns based on count
      const cols = allStrings.length <= 2 ? 'repeat(2, 1fr)' : allStrings.length === 3 ? 'repeat(3, 1fr)' : 'repeat(auto-fill, minmax(180px, 1fr))';

      container.innerHTML = `
        <div style="display:grid; grid-template-columns:${cols}; gap:10px; margin-bottom:8px">
          ${cardsHtml}
        </div>
        <div style="display:flex; align-items:center; gap:8px; padding:8px 10px; background:rgba(46,204,113,0.06); border:1px solid rgba(46,204,113,0.12); border-radius:8px; margin-top:4px">
          <span style="font-size:16px">Σ</span>
          <span style="font-size:12px; color:#f8fafc; font-weight:700">${(totalWp / 1000).toFixed(1)} kWp</span>
          <span style="font-size:10px; color:#94a3b8">· ${allStrings.length} ${allStrings.length === 1 ? 'string' : allStrings.length < 5 ? 'stringi' : 'stringów'}</span>
          <span style="font-size:9px; color:#64748b; margin-left:auto">Dane z Przeglądu → ⚙️ przy stringach PV</span>
        </div>
      `;

    } else {
      // === MANUAL FALLBACK MODE: show input fields ===
      if (subtitle) subtitle.textContent = 'Uzupełnij dane instalacji — wpływa na dokładność prognozy produkcji';

      container.innerHTML = `
        <div style="display:grid; grid-template-columns: repeat(3, 1fr); gap:12px">
          <div>
            <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px">Moc instalacji (kWp)</div>
            <input type="number" id="fc-cfg-kwp" min="0.5" max="100" step="0.1" value="${this._settings.forecast_pv_kwp || 9}" style="width:100%; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.12); border-radius:8px; padding:10px; color:#fff; font-size:16px; font-weight:700; text-align:center; outline:none; transition:border 0.3s" onfocus="this.style.borderColor='#f7b731'" onblur="this.style.borderColor='rgba(255,255,255,0.12)'; this.getRootNode().host._saveForecastConfig()" />
          </div>
          <div>
            <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px">Kąt nachylenia (°)</div>
            <input type="number" id="fc-cfg-tilt" min="0" max="90" step="1" value="${this._settings.forecast_pv_tilt || 35}" style="width:100%; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.12); border-radius:8px; padding:10px; color:#fff; font-size:16px; font-weight:700; text-align:center; outline:none; transition:border 0.3s" onfocus="this.style.borderColor='#00d4ff'" onblur="this.style.borderColor='rgba(255,255,255,0.12)'; this.getRootNode().host._saveForecastConfig()" />
          </div>
          <div>
            <div style="font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px">Kierunek (azymut)</div>
            <select id="fc-cfg-azimuth" style="width:100%; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.12); border-radius:8px; padding:10px; color:#fff; font-size:14px; font-weight:600; text-align:center; outline:none; cursor:pointer; transition:border 0.3s; appearance:none; -webkit-appearance:none" onfocus="this.style.borderColor='#2ecc71'" onblur="this.style.borderColor='rgba(255,255,255,0.12)'; this.getRootNode().host._saveForecastConfig()" onchange="this.getRootNode().host._saveForecastConfig()">
              <option value="0" style="background:#1a1a2e" ${(this._settings.forecast_pv_azimuth || 180) == 0 ? 'selected' : ''}>N — Północ</option>
              <option value="45" style="background:#1a1a2e" ${(this._settings.forecast_pv_azimuth || 180) == 45 ? 'selected' : ''}>NE — Płn-Wsch</option>
              <option value="90" style="background:#1a1a2e" ${(this._settings.forecast_pv_azimuth || 180) == 90 ? 'selected' : ''}>E — Wschód</option>
              <option value="135" style="background:#1a1a2e" ${(this._settings.forecast_pv_azimuth || 180) == 135 ? 'selected' : ''}>SE — Płd-Wsch</option>
              <option value="180" style="background:#1a1a2e" ${(this._settings.forecast_pv_azimuth || 180) == 180 ? 'selected' : ''}>S — Południe ✓</option>
              <option value="225" style="background:#1a1a2e" ${(this._settings.forecast_pv_azimuth || 180) == 225 ? 'selected' : ''}>SW — Płd-Zach</option>
              <option value="270" style="background:#1a1a2e" ${(this._settings.forecast_pv_azimuth || 180) == 270 ? 'selected' : ''}>W — Zachód</option>
              <option value="315" style="background:#1a1a2e" ${(this._settings.forecast_pv_azimuth || 180) == 315 ? 'selected' : ''}>NW — Płn-Zach</option>
            </select>
          </div>
        </div>
        <div style="margin-top:8px; padding:8px 10px; background:rgba(247,183,49,0.06); border:1px solid rgba(247,183,49,0.12); border-radius:8px; display:flex; align-items:center; gap:8px">
          <span style="font-size:14px">💡</span>
          <span style="font-size:10px; color:#f7b731">Dla dokładniejszej prognozy skonfiguruj stringi PV w zakładce <strong>Przegląd</strong> przyciskiem ⚙️ przy każdym stringu. Prognoza automatycznie pobierze parametry z konfiguracji.</span>
        </div>
      `;
    }
  }

  _updateIntegrationStatus() {
    // Inverter
    const inv = this._nm('pv_power');
    const invEl = this.shadowRoot.getElementById('fc-integ-inverter');
    if (invEl) {
      if (inv !== null) { invEl.textContent = '✅ Aktywny'; invEl.style.color = '#2ecc71'; }
      else { invEl.textContent = '⚠️ Brak danych'; invEl.style.color = '#f39c12'; }
    }
    // Battery
    const soc = this._nm('battery_soc');
    const batEl = this.shadowRoot.getElementById('fc-integ-battery');
    if (batEl) {
      if (soc !== null) { batEl.textContent = `✅ SOC: ${soc.toFixed(0)}%`; batEl.style.color = '#2ecc71'; }
      else { batEl.textContent = '❌ Brak'; batEl.style.color = '#e74c3c'; }
    }
    // Energy prices
    const priceEl = this.shadowRoot.getElementById('fc-integ-prices');
    const hasEntso = this._hass?.states?.['sensor.entso_e_aktualna_cena_energii'];
    if (priceEl) {
      if (hasEntso) { priceEl.textContent = '✅ ENTSO-E'; priceEl.style.color = '#2ecc71'; }
      else { priceEl.textContent = '⚠️ Brak'; priceEl.style.color = '#f39c12'; }
    }
    // Forecast.Solar
    const fcEl = this.shadowRoot.getElementById('fc-integ-fcsolar');
    const hasFS = this._hass?.states?.['sensor.power_production_now'];
    if (fcEl) {
      if (hasFS) { fcEl.textContent = '✅ Aktywny'; fcEl.style.color = '#2ecc71'; }
      else { fcEl.textContent = '— nieaktywny'; fcEl.style.color = '#64748b'; }
    }
    // Ecowitt local weather
    const ecoEl = this.shadowRoot.getElementById('fc-integ-ecowitt');
    if (ecoEl) {
      if (this._settings.ecowitt_enabled) {
        const ecoRad = this._hass?.states?.['sensor.ecowitt_solar_radiation_9747'];
        if (ecoRad) {
          ecoEl.innerHTML = `✅ ${parseFloat(ecoRad.state).toFixed(0)} W/m²`;
          ecoEl.style.color = '#2ecc71';
        } else {
          ecoEl.textContent = '⚠️ Brak sensora'; ecoEl.style.color = '#f39c12';
        }
      } else {
        ecoEl.textContent = '— wyłączony'; ecoEl.style.color = '#64748b';
      }
    }
  }

  _updateCalibration() {
    const factor = this._settings.forecast_calibration_factor || 1.0;
    const samples = this._settings.forecast_calibration_samples || 0;
    const mape = this._settings.forecast_calibration_mape;
    this._setText('fc-calib-factor', factor.toFixed(3));
    this._setText('fc-calib-samples', String(samples));
    this._setText('fc-calib-mape', mape != null ? `${mape.toFixed(1)}%` : '—%');

    // Mini calibration chart
    const chartEl = this.shadowRoot.getElementById('fc-calib-chart');
    if (chartEl && samples > 0) {
      const history = this._settings.forecast_calibration_history || [];
      if (history.length > 1) {
        const maxV = Math.max(...history.map(h => Math.max(h.real || 0, h.pred || 0)), 1);
        const w = 100, h2 = 60;
        const step = w / (history.length - 1);
        const realPath = history.map((p, i) => `${i === 0 ? 'M' : 'L'} ${(i * step).toFixed(1)} ${(h2 - (p.real / maxV) * h2 * 0.9).toFixed(1)}`).join(' ');
        const predPath = history.map((p, i) => `${i === 0 ? 'M' : 'L'} ${(i * step).toFixed(1)} ${(h2 - (p.pred / maxV) * h2 * 0.9).toFixed(1)}`).join(' ');
        chartEl.innerHTML = `<svg viewBox="0 0 ${w} ${h2}" style="width:100%;height:100%">
          <path d="${predPath}" fill="none" stroke="#475569" stroke-width="1" stroke-dasharray="3,2"/>
          <path d="${realPath}" fill="none" stroke="#2ecc71" stroke-width="1.5"/>
        </svg>`;
      } else {
        chartEl.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;font-size:10px;color:#475569">Zbyt mało próbek</div>';
      }
    }
  }

  _updateCalibrationFromProduction(predictedKwh) {
    const realPv = this._nm('pv_today') || 0;
    if (realPv < 1 || predictedKwh < 1) return; // Not enough data

    const nowHour = new Date().getHours();
    if (nowHour < 15) return; // Wait until afternoon for meaningful comparison

    const factor = this._settings.forecast_calibration_factor || 1.0;
    const samples = this._settings.forecast_calibration_samples || 0;
    const alpha = 0.1; // Learning rate
    const error = (realPv - predictedKwh) / predictedKwh;
    const newFactor = factor + alpha * error;
    const clampedFactor = Math.max(0.5, Math.min(1.5, newFactor));

    const history = this._settings.forecast_calibration_history || [];
    history.push({ date: new Date().toISOString().slice(0, 10), real: realPv, pred: predictedKwh });
    if (history.length > 30) history.shift();

    const mape = history.length > 0
      ? history.reduce((s, h) => s + Math.abs(h.real - h.pred) / Math.max(h.real, 0.1), 0) / history.length * 100
      : null;

    this._savePanelSettings({
      forecast_calibration_factor: clampedFactor,
      forecast_calibration_samples: samples + 1,
      forecast_calibration_mape: mape,
      forecast_calibration_history: history,
    });

    this._updateCalibration();
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

if (!customElements.get("smartinghome-panel")) {
  customElements.define("smartinghome-panel", SmartingHomePanel);
}
