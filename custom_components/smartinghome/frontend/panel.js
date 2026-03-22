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
  }

  set hass(hass) {
    this._hass = hass;
    if (this.shadowRoot.querySelector(".panel-container")) this._updateAll();
  }
  set panel(p) { this._panel = p; }
  set narrow(n) { this._narrow = n; }

  connectedCallback() {
    this._render();
    this._loadSettings();
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
  _switchTab(tab) {
    this._activeTab = tab;
    this.shadowRoot.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
    this.shadowRoot.querySelectorAll(".tab-content").forEach(c => c.classList.toggle("active", c.dataset.tab === tab));
    if (tab === 'winter') { this._initWinterTab(); this._loadWinterData(); }
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

  async _loadSettings() {
    try {
      const r = await fetch('/local/smartinghome/settings.json?t=' + Date.now());
      if (r.ok) {
        this._settings = await r.json();
        this._updateKeyStatus();
        // Restore model selections
        const gSel = this.shadowRoot.getElementById('sel-gemini-model');
        const aSel = this.shadowRoot.getElementById('sel-anthropic-model');
        if (gSel && this._settings.gemini_model) gSel.value = this._settings.gemini_model;
        if (aSel && this._settings.anthropic_model) aSel.value = this._settings.anthropic_model;
        // Show masked keys as placeholders
        const gInp = this.shadowRoot.getElementById('inp-gemini-key');
        const aInp = this.shadowRoot.getElementById('inp-anthropic-key');
        if (gInp && this._settings.gemini_key_masked) gInp.placeholder = this._settings.gemini_key_masked;
        if (aInp && this._settings.anthropic_key_masked) aInp.placeholder = this._settings.anthropic_key_masked;
        // Restore tariff plan
        const tSel = this.shadowRoot.getElementById('sel-tariff-plan');
        if (tSel && this._settings.tariff_plan) tSel.value = this._settings.tariff_plan;
        // Restore cron settings
        this._loadCronSettings();
        // Show AI-powered HEMS advice if available
        this._updateHEMSFromAI();
        // Subscribe to live AI cron updates
        if (this._hass && !this._cronSub) {
          this._cronSub = this._hass.connection.subscribeEvents((ev) => {
            const d = ev.data;
            if (d.result_key) this._settings[d.result_key] = { text: d.text, timestamp: d.timestamp, provider: d.provider };
            this._updateHEMSFromAI();
            this._updateCronStatus();
          }, "smartinghome_ai_cron_update");
        }
        // Re-apply PV labels and all data after settings loaded
        if (this._hass) this._updateAll();
      }
    } catch(e) { /* file not yet created */ }
  }

  _savePanelSettings(updates) {
    Object.assign(this._settings, updates);
    if (this._hass) {
      this._hass.callService("smartinghome", "save_panel_settings", {
        settings: JSON.stringify(updates)
      });
    }
  }

  _editPvLabel(idx) {
    const current = (this._settings.pv_labels || {})[`pv${idx}`] || `PV${idx}`;
    const newLabel = prompt(`Zmień etykietę PV${idx}:`, current);
    if (newLabel !== null && newLabel.trim()) {
      const labels = this._settings.pv_labels || {};
      labels[`pv${idx}`] = newLabel.trim();
      this._savePanelSettings({ pv_labels: labels });
      const el = this.shadowRoot.getElementById(`pv${idx}-label`);
      if (el) el.textContent = newLabel.trim();
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
    const impVal = this._n(`sensor.grid_import_${s}`) ?? 0;
    const expVal = this._n(`sensor.grid_export_${s}`) ?? 0;
    const selfUse = Math.max(0, pvVal - expVal);

    this._setText("roi-pv", `${pvVal.toFixed(1)} kWh`);
    this._setText("roi-import", `${impVal.toFixed(1)} kWh`);
    this._setText("roi-export", `${expVal.toFixed(1)} kWh`);
    this._setText("roi-selfuse", `${selfUse.toFixed(1)} kWh`);

    // Financial (G13 sensors)
    const costVal = p === "day" ? (this._n("sensor.g13_import_cost_today") ?? 0) : (this._n(`sensor.g13_import_cost_${s}`) ?? 0);
    const revVal = p === "day" ? (this._n("sensor.g13_export_revenue_today") ?? 0) : (this._n(`sensor.g13_export_revenue_${s}`) ?? 0);
    const savVal = p === "day" ? (this._n("sensor.g13_self_consumption_savings_today") ?? 0) : (this._n(`sensor.g13_self_consumption_savings_${s}`) ?? 0);
    const balVal = p === "day" ? (this._n("sensor.g13_net_balance_today") ?? 0) : (this._n(`sensor.g13_net_balance_${s}`) ?? 0);

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

    // ROI calculation
    const invest = this._settings.roi_investment || 0;
    const invInput = this.shadowRoot.getElementById("roi-invest-input");
    if (invInput && !invInput.matches(":focus") && invest) invInput.value = invest;

    // Estimate yearly savings from current period data
    const multiplier = { day: 365, week: 52, month: 12, year: 1 };
    const yearlySavings = (balVal > 0 ? balVal : (revVal + savVal - costVal)) * multiplier[p];
    this._setText("roi-yearly-savings", `${yearlySavings.toFixed(0)} zł/rok`);

    if (invest > 0 && yearlySavings > 0) {
      const paybackYears = invest / yearlySavings;
      this._setText("roi-payback", `~${paybackYears.toFixed(1)} lat`);
      const pctDone = Math.min(100, (1 / paybackYears) * 100); // how much paid back in 1 year
      const pbBar = this.shadowRoot.getElementById("roi-payback-bar");
      if (pbBar) pbBar.style.width = `${pctDone}%`;
      this._setText("roi-payback-pct", `${pctDone.toFixed(0)}% rocznie`);
    } else {
      this._setText("roi-payback", invest > 0 ? "— (brak danych)" : "— (podaj koszt)");
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
      const bal = key === "d" ? (this._n("sensor.g13_net_balance_today") ?? 0) : (this._n(`sensor.g13_net_balance_${suffix}`) ?? 0);
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

    const colors = { off: "#2ecc71", morning: "#e67e22", peak: "#e74c3c", flat: "#3b82f6" };
    const labels = { off: "OFF-PEAK", morning: "PRZEDPOŁUDNIOWA", peak: "SZCZYT", flat: "STAŁA" };

    // Update title
    const titleEl = this.shadowRoot.getElementById("tariff-card-title");
    if (titleEl) titleEl.textContent = `⏰ Taryfa ${tariff}`;

    // Render 24 segments
    const timeline = this.shadowRoot.getElementById("g13-timeline");
    if (timeline) {
      timeline.innerHTML = "";
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

    // Now marker
    const marker = this.shadowRoot.getElementById("g13-now-marker");
    if (marker) {
      const pct = ((hour + minutes / 60) / 24) * 100;
      marker.style.left = `calc(${pct}% - 1px)`;
    }

    // Current zone badge
    const currentZone = getZone(hour);
    const badge = this.shadowRoot.getElementById("v-g13-zone-badge");
    if (badge) {
      badge.textContent = labels[currentZone];
      badge.style.background = colors[currentZone];
      badge.style.color = (currentZone === "peak") ? "#fff" : "#000";
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
      if (tariff === "G11") dayType.textContent = "(stała cena — brak stref)";
      else if (isWeekend && (tariff === "G13" || tariff === "G12w")) dayType.textContent = "(weekend — cały dzień off-peak)";
      else dayType.textContent = "(dzień roboczy)";
    }

    // Season
    const seasonEl = this.shadowRoot.getElementById("v-g13-season");
    if (seasonEl) {
      if (tariff === "G13") {
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

  _saveApiKeys() {
    const gemini = this.shadowRoot.getElementById("inp-gemini-key")?.value || "";
    const anthropic = this.shadowRoot.getElementById("inp-anthropic-key")?.value || "";
    const geminiModel = this.shadowRoot.getElementById("sel-gemini-model")?.value || "gemini-2.5-flash";
    const anthropicModel = this.shadowRoot.getElementById("sel-anthropic-model")?.value || "claude-sonnet-4.6-20260301";
    if (this._hass) {
      // Only send keys that the user actually typed in (non-empty)
      const saveData = {};
      if (gemini) saveData.gemini_api_key = gemini;
      if (anthropic) saveData.anthropic_api_key = anthropic;
      if (Object.keys(saveData).length > 0) {
        this._hass.callService("smartinghome", "save_settings", saveData);
      }
      const updates = { gemini_model: geminiModel, anthropic_model: anthropicModel };
      if (gemini) updates.gemini_key_status = "saved";
      if (anthropic) updates.anthropic_key_status = "saved";
      this._savePanelSettings(updates);
      this._updateKeyStatus();
      const st = this.shadowRoot.getElementById("v-save-status");
      if (st) { st.textContent = "✅ Modele zapisane" + (Object.keys(saveData).length > 0 ? " + klucze zaktualizowane!" : "!"); setTimeout(() => { st.textContent = ""; }, 4000); }
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
    };
    this._savePanelSettings(updates);
    const st = this.shadowRoot.getElementById("v-cron-save-status");
    if (st) { st.textContent = "\u2705 Harmonogram zapisany! Restart HA wymagany."; setTimeout(() => { st.textContent = ""; }, 6000); }
  }

  _loadCronSettings() {
    const s = this._settings;
    const chkH = this.shadowRoot.getElementById("chk-cron-hems");
    const chkR = this.shadowRoot.getElementById("chk-cron-report");
    const chkA = this.shadowRoot.getElementById("chk-cron-anomaly");
    if (chkH && s.cron_hems_enabled !== undefined) chkH.checked = s.cron_hems_enabled;
    if (chkR && s.cron_report_enabled !== undefined) chkR.checked = s.cron_report_enabled;
    if (chkA && s.cron_anomaly_enabled !== undefined) chkA.checked = s.cron_anomaly_enabled;
    const selH = this.shadowRoot.getElementById("sel-cron-hems");
    const selR = this.shadowRoot.getElementById("sel-cron-report");
    const selA = this.shadowRoot.getElementById("sel-cron-anomaly");
    if (selH && s.cron_hems_interval) selH.value = String(s.cron_hems_interval);
    if (selR && s.cron_report_interval) selR.value = String(s.cron_report_interval);
    if (selA && s.cron_anomaly_interval) selA.value = String(s.cron_anomaly_interval);
    this._updateCronStatus();
  }

  _updateCronStatus() {
    const s = this._settings;
    ["hems", "report", "anomaly"].forEach(job => {
      const key = job === "hems" ? "ai_hems_advice" : job === "report" ? "ai_daily_report" : "ai_anomaly_report";
      const el = this.shadowRoot.getElementById(`cron-status-${job}`);
      if (el && s[key] && s[key].timestamp) {
        el.textContent = "\u23f0 Ostatnio: " + s[key].timestamp;
        el.style.color = "#2ecc71";
      }
    });
  }

  _updateHEMSFromAI() {
    const s = this._settings;
    if (s.ai_hems_advice && s.ai_hems_advice.text) {
      const el = this.shadowRoot.getElementById("v-hems-rec");
      if (el) {
        const txt = s.ai_hems_advice.text.replace(/\n/g, '<br>');
        const prov = s.ai_hems_advice.provider || '';
        const ts = s.ai_hems_advice.timestamp || '';
        el.innerHTML = '<div style="font-size:13px;line-height:1.5">' + txt + '</div><div style="font-size:9px;color:#64748b;margin-top:6px">\ud83e\udd16 AI ' + prov + ' \u2022 ' + ts + '</div>';
      }
    }
    if (s.ai_daily_report && s.ai_daily_report.text) {
      const el = this.shadowRoot.getElementById("v-hems-rec-tab");
      if (el) {
        const txt = s.ai_daily_report.text.replace(/\n/g, '<br>');
        const prov = s.ai_daily_report.provider || '';
        const ts = s.ai_daily_report.timestamp || '';
        el.innerHTML = '<div style="font-size:13px;line-height:1.5">' + txt + '</div><div style="font-size:9px;color:#64748b;margin-top:6px">\ud83e\udd16 AI ' + prov + ' \u2022 ' + ts + '</div>';
      }
    }
  }
  // ── Winter Tab (Zima na plusie) ──
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
        '<td style="padding:5px 4px"><div style="background:rgba(255,255,255,0.06);border-radius:4px;height:8px;overflow:hidden"><div data-bar="' + i + '" style="height:100%;width:0%;border-radius:4px;transition:width 0.3s"></div></div></td>';
      tbody.appendChild(tr);
    });
    if (s.winter_pv_kwp) { const k = this.shadowRoot.getElementById('wnt-pv-kwp'); if (k) k.value = s.winter_pv_kwp; }
    if (s.winter_region) { const r = this.shadowRoot.getElementById('wnt-region'); if (r) r.value = s.winter_region; }
    this._recalcWinter();
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
    const annualYield = kwp * (yieldPerKwp[region] || 1000);
    const dist = solarDist[region] || solarDist.center;
    const estEl = this.shadowRoot.getElementById('wnt-est-yearly');
    if (estEl) estEl.textContent = kwp > 0 ? Math.round(annualYield) + ' kWh' : '— kWh';
    const inputs = this.shadowRoot.querySelectorAll('.wnt-cons-input');
    let totalCons = 0, totalPV = 0;
    const monthData = [];
    inputs.forEach((inp, i) => {
      const cons = parseFloat(inp.value) || 0;
      const pv = kwp > 0 ? Math.round(annualYield * dist[i]) : 0;
      const bal = pv - cons;
      totalCons += cons; totalPV += pv;
      monthData.push({ cons, pv, bal, month: i });
      const pvC = this.shadowRoot.querySelector('[data-pv="' + i + '"]');
      const balC = this.shadowRoot.querySelector('[data-bal="' + i + '"]');
      const barE = this.shadowRoot.querySelector('[data-bar="' + i + '"]');
      if (pvC) pvC.textContent = pv > 0 ? pv : '—';
      if (balC) { balC.textContent = cons > 0 ? (bal > 0 ? '+' + bal : '' + bal) : '—'; balC.style.color = bal >= 0 ? '#2ecc71' : '#e74c3c'; }
      if (barE && cons > 0) { const p = Math.min((pv / Math.max(cons, 1)) * 100, 100); barE.style.width = p + '%'; barE.style.background = p >= 100 ? '#2ecc71' : p >= 60 ? '#f7b731' : '#e74c3c'; }
    });
    const totalBal = totalPV - totalCons;
    this._setText('wnt-sum-cons', totalCons > 0 ? totalCons + ' kWh' : '—');
    this._setText('wnt-sum-pv', totalPV > 0 ? totalPV + ' kWh' : '—');
    const sBal = this.shadowRoot.getElementById('wnt-sum-bal');
    if (sBal) { sBal.textContent = totalCons > 0 ? (totalBal > 0 ? '+' : '') + totalBal + ' kWh' : '—'; sBal.style.color = totalBal >= 0 ? '#2ecc71' : '#e74c3c'; }
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
      tips.push('💰 Taryfa G13 — ładuj baterię w nocy (off-peak), zużywaj w szczycie');
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
      winter_region: this.shadowRoot.getElementById('wnt-region')?.value || 'center'
    });
    const st = this.shadowRoot.getElementById('wnt-save-status');
    if (st) { st.textContent = '\u2705 Dane zimowe zapisane!'; setTimeout(() => { st.textContent = ''; }, 4000); }
  }

  _loadWinterData() {
    const s = this._settings;
    if (s.winter_consumption) { const ii = this.shadowRoot.querySelectorAll('.wnt-cons-input'); ii.forEach((inp, i) => { if (s.winter_consumption[i]) inp.value = s.winter_consumption[i]; }); }
    if (s.winter_pv_kwp) { const k = this.shadowRoot.getElementById('wnt-pv-kwp'); if (k) k.value = s.winter_pv_kwp; }
    if (s.winter_region) { const r = this.shadowRoot.getElementById('wnt-region'); if (r) r.value = s.winter_region; }
    this._recalcWinter();
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

  _testApiKey(provider) {
    const btn = this.shadowRoot.getElementById(`test-btn-${provider}`);
    if (btn) { btn.textContent = "⏳ Testowanie..."; btn.disabled = true; }
    // Read key from input field so we test what the user actually entered
    const keyInput = this.shadowRoot.getElementById(`inp-${provider}-key`);
    const keyValue = keyInput?.value || "";
    if (!keyValue) {
      const ind = this.shadowRoot.getElementById(`key-status-${provider}`);
      if (ind) { ind.textContent = "❌ Podaj klucz API"; ind.style.color = "#e74c3c"; }
      if (btn) { btn.textContent = "🧪 Testuj"; btn.disabled = false; }
      return;
    }
    if (this._hass) {
      // First save the key, then test it
      this._hass.callService("smartinghome", "save_settings", {
        [`${provider}_api_key`]: keyValue,
      });
      // Then test with the key
      this._hass.callService("smartinghome", "test_api_key", { provider, api_key: keyValue });
      if (!this._testSub) {
        this._testSub = this._hass.connection.subscribeEvents((ev) => {
          const d = ev.data;
          this._settings[`${d.provider}_key_status`] = d.status;
          this._updateKeyStatus();
          const b = this.shadowRoot.getElementById(`test-btn-${d.provider}`);
          if (b) { b.textContent = "🧪 Testuj"; b.disabled = false; }
        }, "smartinghome_api_key_test");
      }
      setTimeout(() => { if (btn) { btn.textContent = "🧪 Testuj"; btn.disabled = false; } }, 15000);
    }
  }

  // Smart unit formatting
  _pw(w) {
    if (w === null || isNaN(w)) return "—";
    return Math.abs(w) >= 1000 ? `${(w/1000).toFixed(1)} kW` : `${Math.round(w)} W`;
  }

  /* ── Update all ─────────────────────────── */
  _updateAll() { this._updateFlow(); this._updateStats(); this._updateHomeImage(); this._updateG13Timeline(); this._updateSunWidget(); }

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

      // Arc animation — bezier M10,98 Q100,-15 190,98
      const arc = this.shadowRoot.getElementById("ov-sun-arc");
      if (arc) arc.setAttribute("stroke-dashoffset", String(290 * (1 - t)));

      const dot = this.shadowRoot.getElementById("ov-sun-dot");
      if (dot) {
        const p0 = {x:10, y:98}, p1 = {x:100, y:-15}, p2 = {x:190, y:98};
        const cx = (1-t)*(1-t)*p0.x + 2*(1-t)*t*p1.x + t*t*p2.x;
        const cy = (1-t)*(1-t)*p0.y + 2*(1-t)*t*p1.y + t*t*p2.y;
        dot.setAttribute("cx", String(cx));
        dot.setAttribute("cy", String(cy));
        dot.setAttribute("fill", "#f7b731");
        dot.setAttribute("r", "7");
      }
    } else {
      // 🌙 NIGHTTIME
      this._setText("ov-sunrise", fmt(todaySunrise));
      this._setText("ov-sunset", fmt(todaySunset));

      const msToRise = todaySunrise.getTime() - now.getTime();
      const hToRise = Math.floor(msToRise / 3600000);
      const mToRise = Math.floor((msToRise % 3600000) / 60000);
      this._setText("ov-daylight-pct", "☾");
      this._setText("ov-daylight-left", `${hToRise}h ${mToRise}m do wschodu`);
      const statusLabel = this.shadowRoot.getElementById("ov-status-label");
      if (statusLabel) { statusLabel.textContent = "🌙 Noc"; statusLabel.style.color = "#64748b"; }

      // Arc: empty (no daylight progress)
      const arc = this.shadowRoot.getElementById("ov-sun-arc");
      if (arc) arc.setAttribute("stroke-dashoffset", "290");

      // Dot: dim, at right horizon (set position)
      const dot = this.shadowRoot.getElementById("ov-sun-dot");
      if (dot) {
        dot.setAttribute("cx", "190");
        dot.setAttribute("cy", "98");
        dot.setAttribute("fill", "#475569");
        dot.setAttribute("r", "5");
      }
    }
  }

  _updateFlow() {
    const pv = this._nm("pv_power") || 0;
    const load = this._nm("load_power") || 0;
    const grid = this._nm("grid_power") || 0;
    const batt = this._nm("battery_power") || 0;
    const soc = this._nm("battery_soc") || 0;

    // PV
    this._setText("v-pv", this._pw(pv));
    this._setText("v-pv-today", `${this._fm("pv_today")} kWh`);
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
      // Custom label
      const labelEl = this.shadowRoot.getElementById(`pv${i}-label`);
      if (labelEl && pvLabels[`pv${i}`]) labelEl.textContent = pvLabels[`pv${i}`];
    }

    // Home / Load
    this._setText("v-load", this._pw(load));
    this._setText("v-load-l1", `L1: ${this._fm("power_l1", 0)} W`);
    this._setText("v-load-l2", `L2: ${this._fm("power_l2", 0)} W`);
    this._setText("v-load-l3", `L3: ${this._fm("power_l3", 0)} W`);
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

    // Autarky / Self-consumption — calculate from existing data or use smartinghome sensors
    const autarkyVal = this._n("sensor.smartinghome_autarky_today");
    if (autarkyVal !== null) {
      this._setText("v-autarky", `${autarkyVal.toFixed(0)}%`);
    } else {
      // Autarky = (1 - grid_import / total_consumption) × 100
      const impToday = this._nm("grid_import_today") || 0;
      const loadTotal = (this._nm("pv_power") || 0) > 0 ? ((this._nm("load_power") || 0) > 0 ? (this._nm("load_power") || 1) : 1) : 1;
      // Use daily data: pv_today + batt_discharge - batt_charge + import = total consumption; autarky = (total - import) / total
      const pvToday = parseFloat(this._fm("pv_today")) || 0;
      if (pvToday > 0 || impToday > 0) {
        const totalConsumed = pvToday + impToday; // simplified: total consumption ≈ pv generated + imported
        const autarky = totalConsumed > 0 ? Math.min(100, Math.max(0, ((totalConsumed - impToday) / totalConsumed) * 100)) : 0;
        this._setText("v-autarky", `${autarky.toFixed(0)}%`);
      } else {
        this._setText("v-autarky", "—%");
      }
    }
    const selfConsVal = this._n("sensor.smartinghome_self_consumption_today");
    if (selfConsVal !== null) {
      this._setText("v-selfcons", `${selfConsVal.toFixed(0)}%`);
    } else {
      // Self-consumption = (1 - grid_export / pv_generation) × 100
      const expToday = this._nm("grid_export_today") || 0;
      const pvGen = parseFloat(this._fm("pv_today")) || 0;
      if (pvGen > 0) {
        const selfCons = Math.min(100, Math.max(0, ((pvGen - expToday) / pvGen) * 100));
        this._setText("v-selfcons", `${selfCons.toFixed(0)}%`);
      } else {
        this._setText("v-selfcons", "—%");
      }
    }

    // Weather — read from HA weather entity attributes
    const weatherEntities = ["weather.home", "weather.forecast_home", "weather.openweathermap"];
    let weatherState = null;
    for (const we of weatherEntities) {
      if (this._hass?.states[we]) { weatherState = this._hass.states[we]; break; }
    }
    if (weatherState) {
      const wTemp = weatherState.attributes?.temperature;
      const wHumid = weatherState.attributes?.humidity;
      const wCloud = weatherState.attributes?.cloud_coverage;
      this._setText("v-weather", wTemp != null ? `${Math.round(wTemp)}°C` : "");
      this._setText("v-clouds", wCloud != null ? `${Math.round(wCloud)}%` : (wHumid != null ? `${Math.round(wHumid)}%` : "—%"));
    } else {
      // Fallback to mapped sensors
      const wt = this._nm("weather_temp");
      this._setText("v-weather", wt !== null ? `${wt.toFixed(0)}°C` : "");
      this._setText("v-clouds", this._fm("weather_cloud_cover", 0) + "%");
    }

    // Animated flows (GoodWe BT: batt>0=discharge, batt<0=charge)
    this._flow("fl-pv-inv", pv > 10, pv);
    this._flow("fl-inv-load", load > 10, load);
    this._flow("fl-grid-inv", grid > 10, grid);
    this._flow("fl-inv-grid", grid < -10, Math.abs(grid));
    this._flow("fl-inv-batt", batt < -10, Math.abs(batt));  // charging: inverter → battery
    this._flow("fl-batt-inv", batt > 10, batt);               // discharging: battery → inverter

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
      imgEl.setAttribute("data-model", imgName);
      const iconEl = this.shadowRoot.getElementById("v-inv-icon");
      const remoteFallback = `https://smartinghome.pl/wp-content/uploads/2026/03/${imgName === 'deye' ? 'Deye-1' : 'GoodWe-1'}.png`;
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
    // ROI Tab
    this._updateRoi();
    // Overview daily financial KPIs
    const ovCost = this._n("sensor.g13_import_cost_today") ?? this._n("sensor.g13_import_cost_daily") ?? 0;
    const ovRev = this._n("sensor.g13_export_revenue_today") ?? this._n("sensor.g13_export_revenue_daily") ?? 0;
    const ovSav = this._n("sensor.g13_self_consumption_savings_today") ?? this._n("sensor.g13_self_consumption_savings_daily") ?? 0;
    const ovBal = this._n("sensor.g13_net_balance_today") ?? this._n("sensor.g13_net_balance_daily") ?? (ovRev + ovSav - ovCost);
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

    // HEMS Recommendation (tariff tab)
    this._setText("v-hems-rec-tab", this._s("sensor.hems_rce_recommendation") || this._s("sensor.smartinghome_hems_recommendation") || "—");

    // Economics
    const savings = this._n("sensor.g13_self_consumption_savings_today") ?? this._n("sensor.smartinghome_self_consumption_savings_today");
    const expRev = this._n("sensor.g13_export_revenue_today") ?? this._n("sensor.smartinghome_export_revenue_today");
    const impCost = this._n("sensor.g13_import_cost_today") ?? this._n("sensor.smartinghome_import_cost_today");
    const netBal = this._n("sensor.g13_net_balance_today") ?? this._n("sensor.smartinghome_net_balance_today");
    this._setText("v-savings", savings !== null ? savings.toFixed(2) : "—");
    this._setText("v-export-rev", expRev !== null ? expRev.toFixed(2) : "—");
    this._setText("v-import-cost", impCost !== null ? impCost.toFixed(2) : "—");
    const netEl = this.shadowRoot.getElementById("v-net-balance");
    if (netEl && netBal !== null) { netEl.textContent = netBal.toFixed(2); netEl.style.color = netBal >= 0 ? "#2ecc71" : "#e74c3c"; }
    
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
    const pvSurplusVal = this._n("sensor.smartinghome_pv_surplus_power");
    if (pvSurplusVal !== null) {
      this._setText("v-surplus", `${pvSurplusVal.toFixed(0)} W`);
    } else {
      const pvNow = this._nm("pv_power") || 0;
      const loadNow = this._nm("load_power") || 0;
      const surplus = pvNow - loadNow;
      this._setText("v-surplus", `${surplus > 0 ? '+' : ''}${Math.round(surplus)} W`);
      const surplusEl = this.shadowRoot.getElementById("v-surplus");
      if (surplusEl) surplusEl.style.color = surplus > 0 ? "#2ecc71" : "#e74c3c";
    }
    // Net Grid — calculate from daily import/export
    const netGridVal = this._n("sensor.smartinghome_net_grid_today");
    if (netGridVal !== null) {
      this._setText("v-net-grid", `${netGridVal.toFixed(1)} kWh`);
    } else {
      const imp = this._nm("grid_import_today") || 0;
      const exp = this._nm("grid_export_today") || 0;
      const netG = exp - imp;
      this._setText("v-net-grid", `${netG > 0 ? '+' : ''}${netG.toFixed(1)} kWh`);
      const netEl = this.shadowRoot.getElementById("v-net-grid");
      if (netEl) netEl.style.color = netG >= 0 ? "#2ecc71" : "#e74c3c";
    }
    
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
          grid-template-columns: 1fr 1fr 1fr;
          grid-template-rows: auto auto auto;
          gap: 12px; min-height: 540px;
          align-items: start;
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

        /* Corner layout: PV top-left, Home top-right, Inv center, Batt bottom-left, Grid bottom-right */
        .pv-area { grid-column: 1; grid-row: 1; }
        .home-area { grid-column: 3; grid-row: 1; }
        .inv-area { grid-column: 2; grid-row: 2; display: flex; flex-direction: column; align-items: center; justify-content: center; }
        .batt-area { grid-column: 1; grid-row: 2 / 4; align-self: center; }
        .grid-area { grid-column: 3; grid-row: 2 / 4; align-self: center; }
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
          .flow-nodes { grid-template-columns: 1fr 1fr; grid-template-rows: auto; gap: 8px; }
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
        <!-- Top Row: Header+Tabs (2/3) + Sun Widget (1/3) -->
        <div style="display:flex; align-items:stretch; background:rgba(255,255,255,0.03); border-bottom:1px solid rgba(255,255,255,0.06)">
          <!-- Left: Header + Tabs -->
          <div style="flex:2; min-width:0">
            <div class="header" style="position:relative; border-bottom:none">
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
            <div class="tabs" style="border-bottom:none">
              <button class="tab-btn active" data-tab="overview" onclick="this.getRootNode().host._switchTab('overview')">📊 Przegląd</button>
              <button class="tab-btn" data-tab="energy" onclick="this.getRootNode().host._switchTab('energy')">⚡ Energia</button>
              <button class="tab-btn" data-tab="tariff" onclick="this.getRootNode().host._switchTab('tariff')">💰 Taryfy & RCE</button>
              <button class="tab-btn" data-tab="battery" onclick="this.getRootNode().host._switchTab('battery')">🔋 Bateria</button>
              <button class="tab-btn" data-tab="hems" onclick="this.getRootNode().host._switchTab('hems')">🤖 HEMS</button>
              <button class="tab-btn" data-tab="roi" onclick="this.getRootNode().host._switchTab('roi')">📈 Opłacalność</button>
              <button class="tab-btn" data-tab="winter" onclick="this.getRootNode().host._switchTab('winter')">❄️ Zima na plusie</button>
            </div>
          </div>
          <!-- Right: Sun Widget (compact) -->
          <div style="flex:1; display:flex; align-items:center; justify-content:center; gap:8px; padding:4px 10px; border-left:1px solid rgba(255,255,255,0.06); min-width:280px">
            <div style="text-align:center">
              <div style="font-size:8px; color:#64748b; text-transform:uppercase; letter-spacing:0.8px" id="ov-date">—</div>
              <div style="font-size:24px; font-weight:900; color:#fff; letter-spacing:-1px; line-height:1" id="ov-clock">--:--</div>
              <div style="font-size:9px; color:#94a3b8; margin-top:1px" id="ov-day-name">—</div>
            </div>
            <div style="position:relative; width:180px; height:70px; flex-shrink:0">
              <svg viewBox="0 0 200 105" style="width:100%; height:100%">
                <path d="M 10,98 Q 100,-15 190,98" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="1.5" />
                <path id="ov-sun-arc" d="M 10,98 Q 100,-15 190,98" fill="none" stroke="#f7b731" stroke-width="2.5" stroke-dasharray="290" stroke-dashoffset="290" />
                <line x1="5" y1="98" x2="195" y2="98" stroke="rgba(255,255,255,0.08)" stroke-width="0.5" />
                <circle id="ov-sun-dot" cx="100" cy="50" r="7" fill="#f7b731" style="filter:drop-shadow(0 0 8px #f7b731); transition: all 1s ease" />
              </svg>
              <div style="position:absolute; bottom:0; left:2px; font-size:8px; color:#f7b731">🌅 <span id="ov-sunrise">—</span></div>
              <div style="position:absolute; bottom:0; right:2px; font-size:8px; color:#e67e22; text-align:right">🌇 <span id="ov-sunset">—</span></div>
            </div>
            <div style="text-align:right; min-width:60px">
              <div style="font-size:8px; color:#64748b; text-transform:uppercase" id="ov-status-label">Dzień</div>
              <div style="font-size:18px; font-weight:800; color:#f7b731" id="ov-daylight-pct">—%</div>
              <div style="font-size:8px; color:#94a3b8" id="ov-daylight-left">—</div>
            </div>
          </div>
        </div>

        <!-- ═══════ TAB: OVERVIEW ═══════ -->
        <div class="tab-content active" data-tab="overview">
          <div class="flow-wrapper">
            <!-- ORTHOGONAL SVG OVERLAY -->
            <svg class="flow-svg-bg" viewBox="0 0 700 500" preserveAspectRatio="xMidYMid meet">
              <!-- PV (top-left) → Inverter (center): right then down -->
              <path class="fl-line" d="M 150,90 H 350 V 220" />
              <g id="fl-pv-inv" class="fl-dot solar" style="display:none">
                <circle r="5" />
                <animateMotion dur="2s" repeatCount="indefinite" path="M 150,90 H 350 V 220" />
              </g>
              <!-- Inverter → Home (top-right): up then right -->
              <path class="fl-line" d="M 350,220 V 90 H 550" />
              <g id="fl-inv-load" class="fl-dot load-flow" style="display:none">
                <circle r="5" />
                <animateMotion dur="2s" repeatCount="indefinite" path="M 350,220 V 90 H 550" />
              </g>
              <!-- Inverter → Battery (bottom-left): left -->
              <path class="fl-line" d="M 300,280 H 150" />
              <g id="fl-inv-batt" class="fl-dot batt-charge" style="display:none">
                <circle r="5" />
                <animateMotion dur="2s" repeatCount="indefinite" path="M 300,280 H 150" />
              </g>
              <!-- Battery → Inverter: right -->
              <g id="fl-batt-inv" class="fl-dot batt-discharge" style="display:none">
                <circle r="5" />
                <animateMotion dur="2s" repeatCount="indefinite" path="M 150,280 H 300" />
              </g>
              <!-- Grid (bottom-right) → Inverter: left -->
              <path class="fl-line" d="M 400,280 H 550" />
              <g id="fl-grid-inv" class="fl-dot grid-in" style="display:none">
                <circle r="5" />
                <animateMotion dur="2s" repeatCount="indefinite" path="M 550,280 H 400" />
              </g>
              <!-- Inverter → Grid: right -->
              <g id="fl-inv-grid" class="fl-dot grid-out" style="display:none">
                <circle r="5" />
                <animateMotion dur="2s" repeatCount="indefinite" path="M 400,280 H 550" />
              </g>
            </svg>

            <!-- FLOW NODES GRID -->
            <div class="flow-nodes">
              <!-- ☀️ PV AREA (top left) -->
              <div class="pv-area">
                <div class="node" id="pv-node" style="border-color: rgba(247,183,49,0.2); transition: border-color 0.5s, box-shadow 0.5s">
                  <div class="node-title">☀️ Produkcja PV</div>
                  <div class="node-big" style="color:#f7b731" id="v-pv">— W</div>
                  <div class="node-sub" id="v-pv-today">— kWh dziś</div>
                  <div class="pv-strings" style="display:flex; gap:6px; flex-wrap:wrap; margin-top:10px">
                    <div class="pv-string" id="pv1-box"><div class="pv-name" id="pv1-label" onclick="this.getRootNode().host._editPvLabel(1)" style="cursor:pointer" title="Kliknij aby zmienić nazwę">PV1</div><div class="pv-val" id="v-pv1-p">—</div><div class="pv-detail"><span id="v-pv1-v">— V</span> · <span id="v-pv1-a">— A</span></div></div>
                    <div class="pv-string" id="pv2-box"><div class="pv-name" id="pv2-label" onclick="this.getRootNode().host._editPvLabel(2)" style="cursor:pointer" title="Kliknij aby zmienić nazwę">PV2</div><div class="pv-val" id="v-pv2-p">—</div><div class="pv-detail"><span id="v-pv2-v">— V</span> · <span id="v-pv2-a">— A</span></div></div>
                    <div class="pv-string" id="pv3-box" style="display:none"><div class="pv-name" id="pv3-label" onclick="this.getRootNode().host._editPvLabel(3)" style="cursor:pointer">PV3</div><div class="pv-val" id="v-pv3-p">—</div><div class="pv-detail"><span id="v-pv3-v">—</span></div></div>
                    <div class="pv-string" id="pv4-box" style="display:none"><div class="pv-name" id="pv4-label" onclick="this.getRootNode().host._editPvLabel(4)" style="cursor:pointer">PV4</div><div class="pv-val" id="v-pv4-p">—</div><div class="pv-detail"><span id="v-pv4-v">—</span></div></div>
                  </div>
                </div>
              </div>

              <!-- 🏠 ZUZYCIE (top right) -->
              <div class="home-area">
                <div class="node" id="home-node" style="border-color: rgba(46,204,113,0.2); transition: border-color 0.5s, box-shadow 0.5s">
                  <div style="display:flex; align-items:flex-start; gap:12px">
                    <div style="flex:1">
                      <div class="node-title">🏠 Zużycie</div>
                      <div class="node-big" id="v-load">— W</div>
                      <div class="node-detail" style="margin-top:8px">
                        <div id="v-load-l1">L1: — W</div><div id="v-load-l2">L2: — W</div><div id="v-load-l3">L3: — W</div>
                      </div>
                    </div>
                    <div style="flex-shrink:0; max-width:120px">
                      <img id="v-home-img" src="https://smartinghome.pl/wp-content/uploads/2026/03/grafika-domu.png" alt="Dom" style="width:100%; max-height:100px; object-fit:contain; opacity:0.85; border-radius:8px" />
                    </div>
                  </div>
                </div>
              </div>

              <!-- 🔋 BATTERY (left, below PV) -->
              <div class="batt-area">
                <div class="node" id="batt-node" style="border-color: rgba(0,212,255,0.2); transition: border-color 0.5s, box-shadow 0.5s">
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

              <!-- ⚡ INVERTER (center) -->
              <div class="inv-area">
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

              <!-- 🔌 GRID / SIEĆ (right, below Home) -->
              <div class="grid-area">
                <div class="node" id="grid-node" style="border-color: rgba(231,76,60,0.15); transition: border-color 0.5s, box-shadow 0.5s">
                  <div style="display:flex; align-items:flex-start; gap:10px">
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

          <!-- ROW 1: RCE Price Cards -->
          <div style="display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-bottom:14px">
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
          <div style="display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-bottom:14px">
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
          <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; margin-bottom:14px">
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
                <div style="font-size:9px; color:#64748b; text-transform:uppercase; margin-bottom:6px">Harmonogram G13 <span id="g13-day-type" style="color:#f7b731">(dzień roboczy)</span></div>
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
                <div style="font-size:14px; font-weight:600; color:#fff; margin-top:2px" id="v-hems-rec-tab">—</div>
              </div>
            </div>
          </div>

          <!-- ROW 6: Economics -->
          <div style="display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-bottom:14px">
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

            <!-- G13 Strategy -->
            <div style="background:rgba(255,255,255,0.03); border-radius:10px; padding:12px; margin-bottom:12px">
              <div style="font-size:11px; font-weight:700; color:#f7b731; margin-bottom:6px">📋 Strategia G13 + RCE</div>
              <div style="font-size:11px; color:#cbd5e1; line-height:1.6">
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
            <div class="card">
              <div class="card-title">🏗️ Zwrot inwestycji (ROI)</div>
              <div class="dr">
                <span class="lb">Koszt instalacji</span>
                <span class="vl"><input id="roi-invest-input" type="number" value="" placeholder="np. 45000" style="width:80px; background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.15); border-radius:6px; color:#fff; padding:4px 8px; font-size:12px; text-align:right" onchange="this.getRootNode().host._saveRoiInvestment(this.value)"> zł</span>
              </div>
              <div class="dr"><span class="lb">Oszczędności roczne (est.)</span><span class="vl" id="roi-yearly-savings">— zł</span></div>
              <div class="dr"><span class="lb">Zwrot inwestycji</span><span class="vl" id="roi-payback" style="color:#f7b731; font-weight:700">— lat</span></div>
              <div style="margin-top:8px; background:rgba(255,255,255,0.08); border-radius:6px; height:10px; overflow:hidden">
                <div id="roi-payback-bar" style="height:100%; width:0%; background:linear-gradient(90deg,#f7b731,#2ecc71); border-radius:6px; transition:width 0.5s"></div>
              </div>
              <div style="font-size:9px; color:#64748b; text-align:right; margin-top:2px" id="roi-payback-pct">0%</div>
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
                    <th style="padding:6px 6px; width:120px"></th>
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
              <div style="display:flex; gap:16px; flex-wrap:wrap; margin-top:14px">
                <div class="settings-field" style="flex:1; min-width:200px">
                  <label style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">🤖 Model Gemini</label>
                  <select id="sel-gemini-model" style="width:100%; padding:8px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:8px; color:#fff; font-size:12px">
                    <option value="gemini-2.5-flash">Gemini 2.5 Flash (szybki)</option>
                    <option value="gemini-2.5-pro">Gemini 2.5 Pro (zaawansowany)</option>
                    <option value="gemini-3.1">Gemini 3.1 (najnowszy)</option>
                  </select>
                </div>
                <div class="settings-field" style="flex:1; min-width:200px">
                  <label style="font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px">🤖 Model Claude</label>
                  <select id="sel-anthropic-model" style="width:100%; padding:8px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:8px; color:#fff; font-size:12px">
                    <option value="claude-sonnet-4.6-20260301">Sonnet 4.6 (szybki)</option>
                    <option value="claude-opus-4.6-20260301">Opus 4.6 (najpotężniejszy)</option>
                  </select>
                </div>
              </div>
              <button class="save-btn" onclick="this.getRootNode().host._saveApiKeys()">💾 Zapisz klucze API i model</button>
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
              </div>
              <button class="save-btn" style="margin-top:12px" onclick="this.getRootNode().host._saveCronSettings()">💾 Zapisz harmonogram AI</button>
              <div id="v-cron-save-status" style="font-size:11px; color:#2ecc71; margin-top:6px"></div>
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
                  • <strong>Domyślne zdjęcia:</strong> GoodWe i Deye wczytywane automatycznie z smartinghome.pl<br>
                  • <strong>Format:</strong> PNG z przezroczystym tłem (transparent) — najlepszy efekt<br>
                  • <strong>Rozmiar:</strong> 500–800px szerokości, proporcjonalne<br>
                  • <strong>Nazwa pliku:</strong> <code style="color:#f39c12">goodwe.png</code> lub <code style="color:#f39c12">deye.png</code> (nadpisuje domyślne) lub <code style="color:#f39c12">inverter.png</code><br>
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
              <div class="dr"><span class="lb">Wersja integracji</span><span class="vl">1.8.0</span></div>
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
