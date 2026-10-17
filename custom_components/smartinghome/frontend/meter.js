// Smarting HOME — autonomous energy management for Home Assistant
// Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
// SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
// Commercial software, not open source: copying, porting, rewriting (also with AI
// tools), redistributing or removing this notice / the license check requires the
// Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
// SH-ID: 8b1aab899d5a
// Smarting HOME — "Energia i koszty" panel.
// Meter view for homes and businesses, with or without PV: consumption,
// tariff zones, costs, base load, peak vs contracted power, power factor.
// Data: smartinghome/meter/* WebSocket API (hourly recorder statistics,
// e.g. TAURON eLicznik via Tauron AMIplus) + live HA states.

const ZONE_COLORS = {
  morning: "#f7b731",
  afternoon: "#e74c3c",
  peak: "#f7b731",
  off_peak: "#2ecc71",
  flat: "#00d4ff",
};
const FIXED_FALLBACK_LABELS = { moc_umowna: "Moc umowna" };
const SUMMARY_REFRESH_MS = 10 * 60 * 1000;
const VOLT_REFRESH_MS = 30 * 60 * 1000;
const PHASES = ["l1", "l2", "l3"];
// Independent meter (meter_device.py ROLES): live entities picked in Ustawienia
const LIVE_ROLES = [
  ["power_entity", "Moc całkowita", "power", "np. Shelly Pro 3EM — moc całkowita (W)"],
  ...PHASES.map((p) => [`power_${p}`, `Moc ${p.toUpperCase()}`, "power", "W, ze znakiem kierunku"]),
  ...PHASES.map((p) => [`voltage_${p}`, `Napięcie ${p.toUpperCase()}`, "voltage", "V"]),
  ...PHASES.map((p) => [`current_${p}`, `Prąd ${p.toUpperCase()}`, "current", "A, opcjonalnie"]),
  ["pf_entity", "Współczynnik mocy (cos φ)", "power_factor", "pokazuje problem z energią bierną"],
  ...PHASES.map((p) => [`pf_${p}`, `cos φ ${p.toUpperCase()}`, "power_factor", "opcjonalnie"]),
  ["reactive_power_entity", "Moc bierna", "reactive_power", "var — liczy tg φ"],
  ["apparent_power_entity", "Moc pozorna", "apparent_power", "VA — gdy licznik nie podaje mocy biernej"],
  ["frequency_entity", "Częstotliwość", "frequency", "Hz — alert częstotliwości"],
];
const ROLE_LABELS = {
  energy_stat: "Energia pobrana (import)", export_stat: "Energia oddana (eksport)",
  import_stats: "Energia pobrana — suma faz", export_stats: "Energia oddana — suma faz",
  reactive_energy_stat: "Energia bierna pobrana (kvarh)",
  ...Object.fromEntries(LIVE_ROLES.map((r) => [r[0], r[1]])),
};
const unitOf = (st) => st.attributes.unit_of_measurement || "";
const KIND_FILTERS = {
  power: (st) => st.attributes.device_class === "power" || /^(k?W)$/.test(unitOf(st)),
  voltage: (st) => st.attributes.device_class === "voltage" || unitOf(st) === "V",
  current: (st) => st.attributes.device_class === "current" || unitOf(st) === "A",
  power_factor: (st) => st.attributes.device_class === "power_factor" || /power_factor|cos/i.test(st.entity_id),
  reactive_power: (st) => st.attributes.device_class === "reactive_power" || /^k?var$/i.test(unitOf(st)),
  apparent_power: (st) => st.attributes.device_class === "apparent_power" || /^k?VA$/.test(unitOf(st)),
  frequency: (st) => st.attributes.device_class === "frequency" || unitOf(st) === "Hz",
};
const TAN_PHI_LIMIT = 0.4; // typical tg φ0 in DSO tariffs — not verified, shown as a hint only

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

class SmartingHomeMeterPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._data = null;
    this._catalog = null;
    this._view = "overview";
    this._error = "";
    this._loading = false;
    this._gross = null; // null = follow site kind (business netto, home brutto)
    this._draft = null;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._renderShell();
      this._load();
    } else {
      this._updateLive();
    }
  }
  set narrow(n) { this._narrow = n; this._updateNarrow(); }
  set panel(p) { this._panel = p; }

  connectedCallback() {
    if (this._hass && !this.shadowRoot.querySelector(".wrap")) {
      this._renderShell();
      this._renderBody();
    }
    this._timer = setInterval(() => this._load(), SUMMARY_REFRESH_MS);
    this._vis = () => { if (!document.hidden && this._hass) this._load(); };
    document.addEventListener("visibilitychange", this._vis);
    if (window.ResizeObserver && !this._ro) {
      let lastW = 0;
      this._ro = new ResizeObserver((entries) => {
        const w = Math.round(entries[0].contentRect.width);
        if (Math.abs(w - lastW) < 8) return;
        lastW = w;
        clearTimeout(this._roT);
        this._roT = setTimeout(() => { if (this._view === "overview") this._drawCharts(); }, 120);
      });
      this._ro.observe(this);
    }
  }
  disconnectedCallback() {
    clearInterval(this._timer);
    document.removeEventListener("visibilitychange", this._vis);
    if (this._ro) { this._ro.disconnect(); this._ro = null; }
  }

  // ── data ────────────────────────────────────────────────────────────
  async _load() {
    if (!this._hass || this._loading) return;
    this._loading = true;
    try {
      if (!this._catalog) {
        this._catalog = await this._hass.callWS({ type: "smartinghome/meter/tariffs" });
      }
      this._data = await this._hass.callWS({ type: "smartinghome/meter/summary" });
      this._error = "";
    } catch (e) {
      this._error = (e && (e.message || e.code)) || String(e);
    } finally {
      this._loading = false;
    }
    if (this._view === "settings" && this._draft) return; // don't wipe unsaved edits
    this._renderBody();
  }

  get _settings() { return (this._data && this._data.settings) || {}; }
  get _isBusiness() { return (this._settings.site_kind || "business") === "business"; }
  get _showGross() { return this._gross === null ? !this._isBusiness : this._gross; }
  _vat() { return this._showGross ? ((this._catalog && this._catalog.vat) || 1.23) : 1; }

  _zl(netto, digits = 2) {
    if (netto === null || netto === undefined || isNaN(netto)) return "—";
    return (netto * this._vat()).toLocaleString("pl-PL", { minimumFractionDigits: digits, maximumFractionDigits: digits }) + " zł";
  }
  _num(v, digits = 1) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return Number(v).toLocaleString("pl-PL", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  _zoneLabel(z) { return ((this._catalog && this._catalog.zone_labels) || {})[z] || z; }
  _fixedLabel(k) {
    return ((this._catalog && this._catalog.fixed_labels) || {})[k] || FIXED_FALLBACK_LABELS[k] || k;
  }
  _stateNum(entityId) {
    const st = entityId && this._hass && this._hass.states[entityId];
    if (!st) return null;
    const v = parseFloat(st.state);
    return isNaN(v) ? null : { v, unit: st.attributes.unit_of_measurement || "", st };
  }
  // Value in W / var / VA (kW, kvar, kVA scaled)
  _base(entityId) {
    const p = this._stateNum(entityId);
    if (!p) return null;
    return /^k(W|var|VAr|VA)$/i.test(p.unit) ? p.v * 1000 : p.v;
  }
  // Drawn power in W: + import, − export (meter sign from Ustawienia)
  _drawW(entityId) {
    const w = this._base(entityId);
    if (w === null) return null;
    return this._settings.power_sign === "export_positive" ? -w : w;
  }
  _pfVal(entityId) {
    const pf = this._stateNum(entityId);
    return pf ? Math.abs(pf.v > 1.5 ? pf.v / 100 : pf.v) : null;
  }
  _hasPhases() { const s = this._settings; return PHASES.some((p) => s[`power_${p}`] || s[`voltage_${p}`] || s[`current_${p}`]); }
  _hasVoltages() { const s = this._settings; return PHASES.some((p) => s[`voltage_${p}`]); }
  // Total drawn power: the total sensor, else the sum of the phases
  _totalDrawW() {
    const s = this._settings;
    if (s.power_entity) return this._drawW(s.power_entity);
    const ph = PHASES.filter((p) => s[`power_${p}`]).map((p) => this._drawW(s[`power_${p}`]));
    return ph.length && ph.every((x) => x !== null) ? ph.reduce((a, b) => a + b, 0) : null;
  }
  // tg φ = |Q| / P while drawing: from Q, else from S and P, else from cos φ (meter_device.tan_phi)
  _tanPhiLive() {
    const s = this._settings;
    const p = this._totalDrawW();
    if (p !== null && p <= 0) return { v: null, why: "oddawanie do sieci" };
    const q = s.reactive_power_entity ? this._base(s.reactive_power_entity) : null;
    if (p && q !== null) return { v: Math.abs(q) / p, cap: q < 0, from: "Q/P" };
    const sva = s.apparent_power_entity ? this._base(s.apparent_power_entity) : null;
    if (p && sva !== null && sva >= p) return { v: Math.sqrt(sva * sva - p * p) / p, from: "S i P" };
    const pfs = [s.pf_entity, ...PHASES.map((x) => s[`pf_${x}`])].filter(Boolean).map((e) => this._pfVal(e)).filter((x) => x !== null);
    const c = pfs.length ? (s.pf_entity ? pfs[0] : pfs.reduce((a, b) => a + b, 0) / pfs.length) : null;
    if (c && c > 0 && c <= 1) return { v: Math.sqrt(1 - c * c) / c, from: "cos φ" };
    return { v: null };
  }

  // ── shell ───────────────────────────────────────────────────────────
  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; height:100%; color-scheme:dark; }
        * { box-sizing:border-box; margin:0; padding:0; }
        .wrap { min-height:100vh; background:linear-gradient(135deg,#0a1628 0%,#111d35 50%,#0d1f3c 100%);
          color:#e0e6ed; font-family:'Inter','Segoe UI',system-ui,sans-serif; }
        .header { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap;
          padding:10px 20px; background:rgba(255,255,255,0.03); border-bottom:1px solid rgba(255,255,255,0.06);
          position:sticky; top:0; z-index:10; backdrop-filter:blur(8px); }
        .hl { display:flex; align-items:center; gap:10px; min-width:0; }
        .menu-btn { display:none; background:none; border:none; color:#a0aec0; font-size:20px; cursor:pointer; padding:4px 6px; border-radius:6px; }
        .menu-btn.show { display:block; }
        h1 { font-size:17px; font-weight:700; background:linear-gradient(135deg,#00d4ff,#00e676);
          -webkit-background-clip:text; -webkit-text-fill-color:transparent; white-space:nowrap; }
        .sub { font-size:11px; color:#8899aa; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        .hr { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
        .seg { display:flex; background:rgba(0,0,0,0.25); border-radius:8px; padding:2px; }
        .seg button, .btn { border:none; background:transparent; color:#8899aa; font:inherit; font-size:12px;
          padding:6px 11px; border-radius:6px; cursor:pointer; }
        .seg button.on { background:rgba(0,212,255,0.12); color:#00d4ff; }
        .btn { background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.08); color:#cbd5e1; }
        .btn:hover { background:rgba(0,212,255,0.12); color:#00d4ff; }
        .btn.primary { background:linear-gradient(135deg,#00d4ff,#00e676); color:#0a1628; font-weight:700; border:none; }
        .content { padding:18px 20px 40px; max-width:1400px; margin:0 auto; }
        .grid { display:grid; gap:14px; }
        .kpis { grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); }
        .cols { grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr)); margin-top:14px; }
        .card { background:rgba(20,30,48,0.45); border:1px solid rgba(255,255,255,0.07); border-radius:14px; padding:16px; min-width:0; }
        .card.wide { grid-column:1/-1; }
        .card h3 { font-size:13px; font-weight:600; color:#a0aec0; letter-spacing:.02em; margin-bottom:12px;
          display:flex; justify-content:space-between; align-items:baseline; gap:4px 8px; flex-wrap:wrap; }
        .card h3 small { font-weight:400; color:#64748b; font-size:11px; }
        .kpi .lbl { font-size:11px; color:#8899aa; text-transform:uppercase; letter-spacing:.06em; }
        .kpi .val { font-size:28px; font-weight:700; margin:6px 0 2px; font-variant-numeric:tabular-nums; }
        .kpi .val small { font-size:14px; font-weight:500; color:#8899aa; margin-left:3px; }
        .kpi .note { font-size:12px; color:#8899aa; }
        .kpi.warn { border-color:rgba(247,183,49,0.35); background:rgba(247,183,49,0.06); }
        .kpi.bad { border-color:rgba(231,76,60,0.35); background:rgba(231,76,60,0.06); }
        .up { color:#e74c3c; } .down { color:#2ecc71; }
        .banner { border-radius:12px; padding:12px 14px; font-size:13px; line-height:1.5; margin-bottom:14px;
          background:rgba(0,212,255,0.07); border:1px solid rgba(0,212,255,0.25); }
        .banner.warn { background:rgba(247,183,49,0.08); border-color:rgba(247,183,49,0.35); }
        .banner.err { background:rgba(231,76,60,0.08); border-color:rgba(231,76,60,0.35); }
        .banner b { color:#fff; }
        .rows { display:flex; flex-direction:column; gap:7px; font-size:13px; }
        .row { display:flex; justify-content:space-between; gap:10px; align-items:center; }
        .row .k { color:#a0aec0; display:flex; align-items:center; gap:7px; min-width:0; }
        .row .v { font-variant-numeric:tabular-nums; white-space:nowrap; }
        .row.total { border-top:1px solid rgba(255,255,255,0.08); padding-top:8px; margin-top:2px; font-weight:700; }
        .dot { width:9px; height:9px; border-radius:3px; flex:none; }
        .stack { display:flex; height:12px; border-radius:6px; overflow:hidden; background:rgba(255,255,255,0.05); margin:4px 0 12px; }
        .stack span { height:100%; }
        .legend { display:flex; gap:14px; flex-wrap:wrap; font-size:11px; color:#8899aa; margin-top:8px; }
        .legend i { display:inline-block; width:9px; height:9px; border-radius:3px; margin-right:5px; vertical-align:-1px; }
        svg text { fill:#64748b; font-size:10px; font-family:inherit; }
        .gauge { height:14px; border-radius:7px; background:rgba(255,255,255,0.06); position:relative; margin:14px 0 6px; }
        .gauge .fill { position:absolute; inset:0 auto 0 0; border-radius:7px; background:linear-gradient(90deg,#00d4ff,#00e676); }
        .gauge .mark { position:absolute; top:-5px; bottom:-5px; width:2px; background:#e74c3c; }
        .hint { font-size:12px; color:#8899aa; line-height:1.55; margin-top:8px; }
        .hint b { color:#e0e6ed; }
        .muted { color:#64748b; }
        .foot { font-size:11px; color:#64748b; margin-top:18px; line-height:1.6; }
        form .grid2 { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; }
        label.f { display:flex; flex-direction:column; gap:5px; font-size:12px; color:#a0aec0; }
        label.f span.h { font-size:11px; color:#64748b; }
        input, select { padding:8px 10px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12);
          border-radius:8px; color:#fff; font:inherit; font-size:13px; width:100%; }
        select option { background:#1e293b; }
        table.prices { width:100%; border-collapse:collapse; font-size:12px; margin-top:6px; }
        table.prices th { text-align:left; color:#64748b; font-weight:500; padding:4px 6px; }
        table.prices td { padding:4px 6px; }
        table.prices input { padding:6px 8px; }
        .actions { display:flex; gap:8px; justify-content:flex-end; margin-top:16px; flex-wrap:wrap; }
        @media (max-width:600px) {
          .header { padding:8px 12px; } .content { padding:12px 12px 32px; }
          .kpi .val { font-size:24px; }
        }
      </style>
      <div class="wrap">
        <div class="header">
          <div class="hl">
            <button class="menu-btn" title="Menu">☰</button>
            <div style="min-width:0">
              <h1>Energia i koszty</h1>
              <div class="sub" id="sub">Smarting HOME</div>
            </div>
          </div>
          <div class="hr">
            <div class="seg" id="views">
              <button data-view="overview" class="on">Przegląd</button>
              <button data-view="settings">Ustawienia</button>
            </div>
            <div class="seg" id="vat">
              <button data-gross="0">netto</button>
              <button data-gross="1">brutto</button>
            </div>
            <button class="btn" id="refresh" title="Odśwież">⟳</button>
          </div>
        </div>
        <div class="content" id="body"><div class="banner">Wczytywanie danych licznika…</div></div>
      </div>`;
    const root = this.shadowRoot;
    root.querySelector(".menu-btn").addEventListener("click", () =>
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })));
    root.querySelectorAll("#views button").forEach((b) => b.addEventListener("click", () => {
      this._view = b.dataset.view;
      this._draft = null;
      this._renderBody();
      this.shadowRoot.querySelector(".wrap").scrollIntoView({ block: "start" });
    }));
    root.querySelectorAll("#vat button").forEach((b) => b.addEventListener("click", () => {
      this._gross = b.dataset.gross === "1";
      this._renderBody();
    }));
    root.querySelector("#refresh").addEventListener("click", () => { this._vrepTs = 0; this._load(); });
    this._updateNarrow();
  }

  _updateNarrow() {
    const btn = this.shadowRoot && this.shadowRoot.querySelector(".menu-btn");
    if (btn) btn.classList.toggle("show", !!this._narrow);
  }

  _renderBody() {
    const root = this.shadowRoot;
    const body = root.querySelector("#body");
    if (!body) return;
    root.querySelectorAll("#views button").forEach((b) => b.classList.toggle("on", b.dataset.view === this._view));
    root.querySelectorAll("#vat button").forEach((b) => b.classList.toggle("on", (b.dataset.gross === "1") === this._showGross));
    const d = this._data;
    const sub = root.querySelector("#sub");
    if (sub) {
      const src = d && d.source ? d.source.name : "brak źródła danych";
      const tariff = d && d.tariff ? d.tariff.label : "";
      sub.textContent = [tariff, src].filter(Boolean).join(" · ");
    }
    if (this._view === "settings") {
      body.innerHTML = this._settingsHtml();
      this._bindSettings();
      return;
    }
    body.innerHTML = this._overviewHtml();
    this._drawCharts();
    this._updateLive();
    if (this._hasVoltages()) { this._bindVolt(); this._loadVolt(); }
  }

  _drawCharts() {
    const d = this._data;
    if (!d) return;
    this.shadowRoot.querySelectorAll(".chart").forEach((el) => {
      const w = Math.max(280, Math.round(el.clientWidth || 600));
      el.innerHTML = el.dataset.chart === "daily"
        ? this._dailyChart(d.daily || [], d.tariff, w)
        : this._profileChart(d.profile || {}, d.zone_by_hour || [], d.tariff, w);
    });
  }

  // ── overview ───────────────────────────────────────────────────────
  _overviewHtml() {
    if (this._error && !this._data) {
      return `<div class="banner err"><b>Nie udało się wczytać danych.</b> ${esc(this._error)}</div>`;
    }
    const d = this._data;
    if (!d) return `<div class="banner">Wczytywanie danych licznika…</div>`;
    if (!d.source) {
      return `<div class="banner warn"><b>Brak danych z licznika.</b><br>
        Podłącz licznik, który zapisuje statystyki energii w kWh — najprościej integrację
        <b>Tauron AMIplus</b> (konto w eLiczniku TAURON Dystrybucji) albo własny licznik w rozdzielnicy —
        <b>Shelly EM / 3EM / Pro 3EM</b> lub <b>Eastron SDM</b> przez Modbus (szablon w docs/eastron_sdm_modbus.yaml).
        Potem w <b>Ustawieniach</b> tego panelu kliknij <b>„Wykryj licznik”</b> albo wybierz encje ręcznie.</div>
        ${this._hasPhases() || this._settings.power_entity ? `<div class="grid cols">${this._phasesCard()}${this._qualityCard()}${this._voltCard()}</div>` : ""}`;
    }
    const banners = [];
    if (this._error) banners.push(`<div class="banner err">Ostatnie odświeżenie nie powiodło się: ${esc(this._error)}</div>`);
    if (!d.hours_count) {
      banners.push(`<div class="banner warn"><b>Czekamy na pierwsze dane z licznika</b> (${esc(d.source.name)}).<br>
        Nowe konto w eLiczniku pobiera dane 1–3 dni, a eLicznik zwykle pokazuje je z opóźnieniem około doby.
        Panel uzupełni się sam.</div>`);
    }
    if (d.tariff && d.tariff.needs_input) {
      banners.push(`<div class="banner warn"><b>Uzupełnij ceny z faktury</b> — dla taryfy ${esc(d.tariff.id)} nie ma
        domyślnych stawek. Ustawienia → Ceny z faktury.</div>`);
    }
    const s = this._settings;
    const cm = d.current_month || {};
    const pm = d.previous_month || {};
    const costHidden = d.tariff && d.tariff.needs_input;
    const bl = d.baseload || {};
    const alertW = Number(s.baseload_alert_w) || 0;
    const blCls = bl.w && alertW && bl.w > alertW * 2 ? "bad" : bl.w && alertW && bl.w > alertW ? "warn" : "";
    const pmFull = pm.total && pm.complete !== false;
    const delta = cm.forecast_total && pmFull ? (cm.forecast_total - pm.total) / pm.total * 100 : null;

    const kpis = `
      <div class="grid kpis">
        <div class="card kpi" id="kpi-live">
          <div class="lbl">Moc teraz</div>
          <div class="val" id="live-val">—</div>
          <div class="note" id="live-note">${s.power_entity || this._hasPhases() ? "" : "Dodaj czujnik mocy w Ustawieniach"}</div>
        </div>
        ${d.exchange ? this._exportKpi(d.exchange.current_month) : ""}
        <div class="card kpi">
          <div class="lbl">Ten miesiąc</div>
          <div class="val">${this._num(cm.kwh, 0)}<small>kWh</small></div>
          <div class="note">${costHidden ? "uzupełnij ceny" : `${this._zl(cm.total)} z opłatami stałymi`} · ${cm.days_with_data || 0}/${cm.days || "—"} dni</div>
        </div>
        <div class="card kpi">
          <div class="lbl">Prognoza rachunku</div>
          <div class="val">${costHidden || cm.forecast_total === undefined ? "—" : this._zl(cm.forecast_total, 0)}</div>
          <div class="note">${cm.forecast_total === undefined && !costHidden ? "po 3 dniach danych · " : ""}${pmFull && !costHidden ? `poprzedni miesiąc ${this._zl(pm.total, 0)}` : "za cały miesiąc"}
            ${delta !== null && isFinite(delta) && !costHidden ? ` · <span class="${delta > 0 ? "up" : "down"}">${delta > 0 ? "▲" : "▼"} ${this._num(Math.abs(delta), 0)}%</span>` : ""}</div>
        </div>
        <div class="card kpi ${blCls}">
          <div class="lbl">Stałe obciążenie</div>
          <div class="val">${bl.w !== null && bl.w !== undefined ? this._num(bl.w, 0) : "—"}<small>W</small></div>
          <div class="note">${bl.year_cost ? `≈ ${this._num(bl.year_kwh, 0)} kWh / ${this._zl(bl.year_cost, 0)} rocznie` : "pobór w nocy (1:00–5:00)"}</div>
        </div>
      </div>`;

    return `${banners.join("")}${kpis}
      <div class="grid cols">
        ${this._costCard(cm, "Koszt w tym miesiącu", costHidden)}
        ${this._baseloadCard(bl, alertW)}
        <div class="card wide"><h3>Zużycie dzień po dniu <small>ostatnie 31 dni · strefy taryfy</small></h3><div class="chart" data-chart="daily"></div></div>
        <div class="card wide"><h3>Profil doby <small>średni pobór w każdej godzinie · ostatnie 4 tygodnie</small></h3><div class="chart" data-chart="profile"></div></div>
        ${this._exchangeCard(d)}
        ${this._phasesCard()}
        ${this._peakCard(d)}
        ${this._qualityCard()}
        ${this._voltCard()}
        ${pm.kwh ? this._costCard(pm, "Poprzedni miesiąc", costHidden) : ""}
      </div>
      <div class="foot">
        Źródło: ${esc(d.source.name)} (${esc(d.source.id)})${d.export_source ? ` · eksport: ${esc(d.export_source.name)}` : ""} · dane do ${d.data_until ? esc(d.data_until.replace("T", " ")) : "—"}<br>
        ${d.cost_basis === "balanced" ? "Koszty liczone od poboru po bilansowaniu godzinowym (import − eksport w każdej godzinie), jak w net-billingu.<br>" : ""}
        Kwoty ${this._showGross ? "brutto (z VAT 23%)" : "netto"} · ceny z Ustawień panelu (domyślnie taryfy TAURON 2026) ·
        ${d.fixed_breakdown && d.fixed_breakdown.bierna ? "energia bierna jako średnia z faktury" : "bez energii biernej"} i rozliczeń korygujących. Moc szczytowa to średnia godzinowa — licznik rozlicza maksimum 15-minutowe.
      </div>`;
  }

  _costCard(m, title, hidden) {
    const t = this._data.tariff;
    if (!m || !t) return "";
    const zones = (t.zones || []).map((z) => ({ z, ...(m.zones && m.zones[z]) || { kwh: 0, energy: 0, dist: 0 } }));
    const totalKwh = zones.reduce((a, b) => a + (b.kwh || 0), 0) || 0;
    const bar = zones.map((z) => `<span style="width:${totalKwh ? z.kwh / totalKwh * 100 : 0}%;background:${ZONE_COLORS[z.z] || "#00d4ff"}"></span>`).join("");
    const zoneRows = zones.map((z) => `
      <div class="row"><span class="k"><span class="dot" style="background:${ZONE_COLORS[z.z] || "#00d4ff"}"></span>${esc(this._zoneLabel(z.z))}</span>
      <span class="v">${this._num(z.kwh, 1)} kWh · ${totalKwh ? this._num(z.kwh / totalKwh * 100, 0) : 0}%${hidden ? "" : ` · ${this._zl((z.energy || 0) + (z.dist || 0))}`}</span></div>`).join("");
    const fixed = this._data.fixed_breakdown || {};
    const fixedRows = hidden ? "" : Object.entries(fixed).map(([k, v]) =>
      `<div class="row"><span class="k muted">${esc(this._fixedLabel(k))}</span><span class="v muted">${this._zl(v)}</span></div>`).join("");
    const money = hidden ? "" : `
      <div class="row" style="margin-top:6px"><span class="k">Energia czynna</span><span class="v">${this._zl(m.energy)}</span></div>
      <div class="row"><span class="k">Dystrybucja zmienna</span><span class="v">${this._zl(m.dist)}</span></div>
      <div class="row"><span class="k">${m.complete === false ? `Opłaty stałe (${m.days_with_data} z ${m.days} dni)` : m.fixed_month && m.fixed_month !== m.fixed ? "Opłaty stałe (do dziś)" : "Opłaty stałe (miesiąc)"}</span><span class="v">${this._zl(m.fixed)}</span></div>
      ${fixedRows}
      <div class="row total"><span class="k" style="color:#e0e6ed">Razem</span><span class="v">${this._zl(m.total)}</span></div>
      ${m.kwh && m.days_with_data >= 3 ? `<div class="hint">Średnio <b>${this._zl(m.total / m.kwh)}</b> za 1 kWh wraz z opłatami stałymi.</div>` : ""}`;
    const partial = m.complete === false
      ? `<div class="hint">Licznik ma dane tylko z ${m.days_with_data} z ${m.days} dni tego miesiąca — to nie jest cały rachunek.</div>` : "";
    return `<div class="card"><h3>${esc(title)} <small>${this._num(m.kwh, 1)} kWh · ${m.days_with_data || 0} dni z danymi</small></h3>
      <div class="stack">${bar}</div><div class="rows">${zoneRows}${money}</div>${partial}</div>`;
  }

  _baseloadCard(bl, alertW) {
    if (!bl || bl.w === null || bl.w === undefined) {
      return `<div class="card"><h3>Stałe obciążenie</h3><div class="hint">Pojawi się po pierwszych nocach z danymi z licznika.</div></div>`;
    }
    const over = alertW && bl.w > alertW;
    return `<div class="card"><h3>Stałe obciążenie <small>urządzenia pracujące całą dobę</small></h3>
      <div class="rows">
        <div class="row"><span class="k">W nocy (1:00–5:00, mediana)</span><span class="v"><b>${this._num(bl.w, 0)} W</b></span></div>
        <div class="row"><span class="k">Średnio w dni wolne</span><span class="v">${this._num(bl.free_day_avg_w, 0)} W</span></div>
        <div class="row"><span class="k">Średnio w godzinach pracy (8–16)</span><span class="v">${this._num(bl.work_hours_avg_w, 0)} W</span></div>
        <div class="row"><span class="k">Rocznie</span><span class="v">${this._num(bl.year_kwh, 0)} kWh · ${this._zl(bl.year_cost, 0)}</span></div>
      </div>
      <div class="hint">${over
        ? `Tło przekracza próg <b>${this._num(alertW, 0)} W</b>. Każde 100 W pracujące non stop to około <b>876 kWh</b> rocznie. Sprawdź serwery, UPS-y, bojler, klimatyzację w czuwaniu i zasilacze — wyłączając obwody po kolei i obserwując moc na żywo.`
        : `Tło poniżej progu ${this._num(alertW, 0)} W — dobrze.`}${this._hass && this._hass.panels && this._hass.panels.smartinghome
        ? "<br>Z fotowoltaiką i magazynem to pobór <b>z sieci</b> po bilansowaniu — obejmuje też nocne ładowanie baterii z sieci, nie tylko urządzenia."
        : ""}</div></div>`;
  }

  _peakCard(d) {
    const kw = Number(d.contract_kw) || 0;
    const pc = d.peak && d.peak.current_month, pp = d.peak && d.peak.previous_month;
    const peak = pc && pp ? (pc.kw >= pp.kw ? pc : pp) : pc || pp || null;
    if (!kw && !peak) return "";
    const pk = peak ? peak.kw : 0;
    const scale = Math.max(kw, pk) * 1.1 || 1;
    let hint = "";
    if (kw && pk) {
      const ratio = pk / kw;
      const fixedPart = this._data.fixed_breakdown && this._data.fixed_breakdown.moc_umowna;
      hint = ratio < 0.35
        ? `Najwyższy pobór to <b>${this._num(ratio * 100, 0)}%</b> mocy umownej. Moc umowną da się zwykle obniżyć
           (wniosek do operatora, czasem wymiana zabezpieczenia) — dziś kosztuje <b>${this._zl(fixedPart)}</b> miesięcznie.
           Sprawdź najpierw szczyty zimowe i 15-minutowe w eLiczniku (zakładka Moc).`
        : `Najwyższy pobór to ${this._num(ratio * 100, 0)}% mocy umownej.`;
    } else if (!kw) {
      hint = `Wpisz moc umowną z faktury w Ustawieniach, żeby porównać ją ze szczytami.`;
    }
    return `<div class="card"><h3>Moc szczytowa a moc umowna <small>${peak ? "maks. średnia godzinowa " + esc(peak.at.replace("T", " ")) : ""}</small></h3>
      <div class="rows">
        <div class="row"><span class="k">Najwyższy pobór (ten / poprzedni miesiąc)</span>
          <span class="v"><b>${this._num(d.peak && d.peak.current_month ? d.peak.current_month.kw : null, 2)}</b> / ${this._num(d.peak && d.peak.previous_month ? d.peak.previous_month.kw : null, 2)} kW</span></div>
        <div class="row"><span class="k">Moc umowna</span><span class="v">${kw ? this._num(kw, 1) + " kW" : "—"}</span></div>
      </div>
      <div class="gauge"><div class="fill" style="width:${pk / scale * 100}%"></div>${kw ? `<div class="mark" style="left:${kw / scale * 100}%" title="Moc umowna"></div>` : ""}</div>
      <div class="hint">${hint}</div></div>`;
  }

  _qualityCard() {
    const s = this._settings;
    const d = this._data || {};
    const hasLive = s.pf_entity || s.reactive_power_entity || s.apparent_power_entity || PHASES.some((p) => s[`pf_${p}`]);
    const r = d.reactive;
    if (!hasLive && !r && !this._isBusiness) return ""; // reactive energy is billed in C tariffs only
    const invoice = d.fixed_breakdown && d.fixed_breakdown.bierna;
    if (!hasLive && !r) {
      return `<div class="card"><h3>Jakość energii · energia bierna</h3>
        <div class="hint">Licznik w eLiczniku nie pokazuje współczynnika mocy na bieżąco, a w taryfach C operator
        dolicza <b>energię bierną pojemnościową</b> (zasilacze, UPS-y, LED, kondensatory w rozdzielnicy) —
        potrafi to być nawet kilkadziesiąt procent rachunku.${invoice
          ? ` Tutaj według faktury to <b>${this._zl(invoice)}</b> miesięcznie.` : ""}<br><br>
        Własny licznik w rozdzielnicy (<b>Shelly Pro 3EM</b>, <b>Eastron SDM630</b>) pokaże moc, <b>cos φ</b>
        i moc bierną na żywo. Ustawienia → „Wykryj licznik”.</div></div>`;
    }
    const pfMain = s.pf_entity ? this._pfVal(s.pf_entity) : null;
    const good = pfMain !== null && pfMain >= 0.93;
    const month = (m, label) => !m || !m.hours ? "" : `
      <div class="row"><span class="k">${label} <span class="muted">${m.hours} h</span></span>
        <span class="v" style="white-space:normal;text-align:right">tg φ <b class="${m.tan_phi !== null && m.tan_phi > TAN_PHI_LIMIT ? "up" : "down"}">${this._num(m.tan_phi, 2)}</b>
        · ${this._num(m.q_ind_kvarh, 1)} kvarh ind.${m.q_cap_kvarh ? ` · <span class="up">${this._num(m.q_cap_kvarh, 1)} kvarh poj.</span>` : ""}</span></div>
      ${m.hours_over ? `<div class="row"><span class="k muted">godziny z tg φ &gt; ${this._num(TAN_PHI_LIMIT, 1)}</span><span class="v muted">${m.hours_over} z ${m.hours}</span></div>` : ""}`;
    const cm = r && r.current_month, pm = r && r.previous_month;
    const warnMonth = (cm && cm.tan_phi !== null && cm.tan_phi > TAN_PHI_LIMIT) || (cm && cm.q_cap_kvarh > 0);
    return `<div class="card"><h3>Jakość energii · energia bierna <small>${r && r.source === "energy" ? "z licznika kvarh" : r ? "ze średnich godzinowych P i Q" : "na żywo"}</small></h3>
      <div class="rows">
        ${s.pf_entity ? `<div class="row"><span class="k">Współczynnik mocy (cos φ) teraz</span>
          <span class="v" id="pf-val"><b class="${good ? "down" : "up"}">${pfMain !== null ? this._num(pfMain, 2) : "—"}</b></span></div>` : ""}
        <div class="row"><span class="k">tg φ teraz <span class="muted" id="tg-from"></span></span><span class="v" id="tg-live">—</span></div>
        ${s.reactive_power_entity ? `<div class="row"><span class="k">Moc bierna teraz</span><span class="v" id="q-live">—</span></div>` : ""}
        ${month(cm, "Ten miesiąc")}${month(pm, "Poprzedni miesiąc")}
        ${invoice ? `<div class="row"><span class="k muted">Energia bierna z faktury</span><span class="v muted">${this._zl(invoice)} / mies.</span></div>` : ""}
      </div>
      <div class="hint">tg φ = moc bierna / moc czynna w godzinach poboru. W taryfach operatorów zwykle próg to
        <b>tg φ₀ = ${this._num(TAN_PHI_LIMIT, 1)}</b> (sprawdź w umowie i taryfie OSD) — powyżej niego płaci się za energię
        bierną indukcyjną, a za <b>pojemnościową</b> zwykle za każdą kvarh. Panel nie wycenia tej opłaty — porównaj z fakturą.
        ${warnMonth ? `<br><b class="up">W tym miesiącu ${cm.q_cap_kvarh > 0 ? "licznik widzi energię bierną pojemnościową" : `tg φ przekracza ${this._num(TAN_PHI_LIMIT, 1)}`}.</b>
          Elektryk może dobrać kompensację (dławik przy pojemnościowej, kondensatory przy indukcyjnej) — pomiar na fazach pokaże, gdzie szukać.` : ""}
        ${pfMain !== null && !good ? "<br>Niski cos φ przy małym obciążeniu zwykle oznacza energię bierną pojemnościową (zasilacze, UPS-y, LED)." : ""}
        ${r && r.source === "energy" ? "<br>Licznik kvarh tylko rośnie — nie odróżnia energii indukcyjnej od pojemnościowej." : ""}</div></div>`;
  }

  _exportKpi(m) {
    if (!m) return "";
    return `<div class="card kpi">
      <div class="lbl">Oddane do sieci</div>
      <div class="val">${this._num(m.export_kwh, 0)}<small>kWh</small></div>
      <div class="note">ten miesiąc · saldo ${m.net_kwh > 0 ? "pobór" : "nadwyżka"} ${this._num(Math.abs(m.net_kwh), 0)} kWh</div>
    </div>`;
  }

  _exchangeCard(d) {
    const x = d.exchange;
    if (!x) return "";
    const col = (m) => m && m.hours ? m : null;
    const cm = col(x.current_month), pm = col(x.previous_month);
    const row = (label, k, digits = 1) => `<div class="row"><span class="k">${label}</span>
      <span class="v">${cm ? this._num(cm[k], digits) : "—"} / ${pm ? this._num(pm[k], digits) : "—"} kWh</span></div>`;
    return `<div class="card"><h3>Wymiana z siecią <small>ten / poprzedni miesiąc · licznik ${esc(this._settings.meter_device || "")}</small></h3>
      <div class="rows">
        ${row("Pobrane z sieci (import)", "import_kwh")}
        ${row("Oddane do sieci (eksport)", "export_kwh")}
        ${row("Saldo (import − eksport)", "net_kwh")}
        <div class="row total"><span class="k" style="color:#e0e6ed">Po bilansowaniu godzinowym</span><span class="v"></span></div>
        ${row("pobór", "balanced_import_kwh")}
        ${row("nadwyżka oddana", "balanced_export_kwh")}
      </div>
      <div class="hint">Bilansowanie godzinowe: w każdej godzinie liczy się tylko różnica poboru i oddania — tak
        rozlicza się prosument w net-billingu. Licznik w rozdzielnicy mierzy to samo miejsce co licznik operatora,
        więc działa też przy mikroinstalacji bez integracji falownika.${d.cost_basis === "balanced" ? " Koszty w panelu liczone są od poboru po bilansowaniu." : ""}</div></div>`;
  }

  _phasesCard() {
    if (!this._hasPhases()) return "";
    return `<div class="card"><h3>Fazy L1 · L2 · L3 <small>na żywo · min–maks dziś</small></h3>
      <div id="phases-body">${this._phasesBody()}</div></div>`;
  }

  _phasesBody() {
    const s = this._settings;
    const today = (this._data && this._data.phases_today) || {};
    const fmtW = (w) => w === null ? "—" : Math.abs(w) >= 1000 ? `${this._num(w / 1000, 2)} kW` : `${this._num(w, 0)} W`;
    const live = {};
    const rows = PHASES.map((p) => {
      const w = s[`power_${p}`] ? this._drawW(s[`power_${p}`]) : null;
      const v = s[`voltage_${p}`] ? this._base(s[`voltage_${p}`]) : null;
      const a = s[`current_${p}`] ? this._base(s[`current_${p}`]) : null;
      const pf = s[`pf_${p}`] ? this._pfVal(s[`pf_${p}`]) : null;
      live[p] = { w, v };
      const tv = today[`voltage_${p}`] || {};
      const lo = tv.min !== undefined && tv.min !== null ? Math.min(tv.min, v ?? tv.min) : v;
      const hi = tv.max !== undefined && tv.max !== null ? Math.max(tv.max, v ?? tv.max) : v;
      const vCls = hi !== null && hi > 253 ? "up" : "";
      return `<tr><td><b>${p.toUpperCase()}</b></td>
        <td class="${w !== null && w < 0 ? "down" : ""}">${fmtW(w)}</td>
        <td>${v !== null ? this._num(v, 1) + " V" : "—"}</td>
        <td class="${vCls}">${lo !== null && lo !== undefined ? `${this._num(lo, 1)}–${this._num(hi, 1)} V` : "—"}</td>
        <td>${a !== null ? this._num(a, 1) + " A" : "—"}</td>
        <td>${pf !== null ? this._num(pf, 2) : "—"}</td></tr>`;
    }).join("");
    const vs = PHASES.map((p) => live[p].v).filter((x) => x !== null && x > 100);
    const ws = PHASES.map((p) => [p, live[p].w]).filter((x) => x[1] !== null);
    let asym = "";
    if (vs.length >= 2) {
      const avg = vs.reduce((a, b) => a + b, 0) / vs.length;
      const unb = Math.max(...vs.map((x) => Math.abs(x - avg))) / avg * 100;
      asym += `<div class="row"><span class="k">Asymetria napięć</span><span class="v ${unb > 2 ? "up" : ""}">${this._num(unb, 2)}% · różnica ${this._num(Math.max(...vs) - Math.min(...vs), 1)} V</span></div>`;
    }
    if (ws.length >= 2) {
      const hi = ws.reduce((a, b) => (b[1] > a[1] ? b : a)), lo = ws.reduce((a, b) => (b[1] < a[1] ? b : a));
      asym += `<div class="row"><span class="k">Rozkład obciążenia</span><span class="v">${hi[0].toUpperCase()} − ${lo[0].toUpperCase()} = ${fmtW(hi[1] - lo[1])}</span></div>`;
    }
    const tp = today.power_entity || {};
    return `<table class="prices"><thead><tr><th>Faza</th><th>Moc</th><th>Napięcie</th><th>Dziś</th><th>Prąd</th><th>cos φ</th></tr></thead>
      <tbody>${rows}</tbody></table>
      <div class="rows" style="margin-top:8px">${asym}
        ${tp.max !== undefined && tp.max !== null ? `<div class="row"><span class="k muted">Moc całkowita dziś (min–maks)</span><span class="v muted">${fmtW(tp.min)} – ${fmtW(tp.max)}</span></div>` : ""}</div>
      <div class="hint">Moc ujemna (zielona) = oddawanie do sieci. Asymetria napięć powyżej 2% (PN-EN 50160) i duża różnica
        obciążenia faz to sygnał do przełożenia odbiorników między fazami albo zgłoszenia do operatora.</div>`;
  }

  // ── grid voltage from the meter (smartinghome/voltage/report, source "meter") ──
  _voltCard() {
    if (!this._hasVoltages()) return "";
    return `<div class="card wide"><h3>Napięcie sieci z licznika <small>ostatnie 30 dni · PN-EN 50160</small></h3>
      <div id="vrep">${this._voltBody()}</div><div id="vrep-out"></div></div>`;
  }

  _voltBody() {
    const r = this._vrep;
    if (!r) return `<div class="hint">Wczytywanie raportu napięcia…</div>`;
    if (r.error) return `<div class="hint">Raport napięcia niedostępny: ${esc(r.error)}</div>`;
    if (r.source !== "meter") {
      return `<div class="hint">Brak statystyk napięć z licznika (encje napięcia muszą mieć state_class „measurement”
        i być włączone). Pojawią się po pierwszej pełnej godzinie pomiarów.</div>`;
    }
    const s = r.summary || {};
    const lim = r.limit_v || 253;
    const ps = (r.phases && r.phases.summary) || {};
    const weeks = (r.weeks || []).map((w) => `${esc(w.week)}: <b class="${w.ok ? "down" : "up"}">${this._num(w.within_pct, 1)}%</b>`).join(" · ");
    const actions = r.export_allowed === false
      ? `<span class="muted">CSV i treść reklamacji — funkcja PRO</span>`
      : `<button type="button" class="btn" id="vrep-csv">⬇ CSV</button> <button type="button" class="btn" id="vrep-txt">Treść reklamacji</button>`;
    return `<div class="grid kpis">
        <div class="card kpi ${s.max_v > lim ? "bad" : ""}"><div class="lbl">Maks. napięcie</div><div class="val">${this._num(s.max_v, 1)}<small>V</small></div><div class="note">od ${esc(s.first_day || "—")}</div></div>
        <div class="card kpi ${s.days_over ? "warn" : ""}"><div class="lbl">Dni &gt; ${lim} V</div><div class="val">${s.days_over ?? 0}<small>/ ${s.days ?? 0}</small></div><div class="note">godzin: ${s.hours_max_over ?? 0}</div></div>
        <div class="card kpi ${s.ten_min_over ? "bad" : ""}"><div class="lbl">Średnie 10-min &gt; ${lim} V</div><div class="val">${s.ten_min_over ?? 0}</div><div class="note">ostatnie 10 dni</div></div>
        ${ps.hours ? `<div class="card kpi ${ps.max_unbalance_pct > 2 ? "warn" : ""}"><div class="lbl">Asymetria faz</div><div class="val">${this._num(ps.max_unbalance_pct, 2)}<small>%</small></div><div class="note">śr. różnica ${this._num(ps.avg_spread, 1)} V</div></div>` : ""}
      </div>
      ${weeks ? `<div class="hint">Tygodnie (95% średnich 10-min w 207–253 V): ${weeks}</div>` : ""}
      <div class="actions" style="justify-content:flex-start">${actions}</div>
      <div class="hint">Źródło: statystyki napięć z licznika w Home Assistant. ${r.grid_only
        ? "Obiekt bez falownika — wysokie napięcie pochodzi z sieci operatora."
        : "Przy falowniku jego pomiar pozostaje domyślny w raporcie głównego panelu; tu widać licznik jako drugie źródło."}</div>`;
  }

  async _loadVolt(force = false) {
    if (!this._hasVoltages() || !this._hass) return;
    if (!force && this._vrepTs && Date.now() - this._vrepTs < VOLT_REFRESH_MS) return;
    this._vrepTs = Date.now();
    try {
      this._vrep = await this._hass.callWS({ type: "smartinghome/voltage/report", days: 30, source: "meter" });
    } catch (e) {
      this._vrep = { error: (e && (e.message || e.code)) || String(e) };
    }
    const el = this.shadowRoot.querySelector("#vrep");
    if (el) { el.innerHTML = this._voltBody(); this._bindVolt(); }
  }

  _bindVolt() {
    const root = this.shadowRoot;
    const out = root.querySelector("#vrep-out");
    const run = async (kind) => {
      try {
        const x = await this._hass.callWS({ type: "smartinghome/voltage/export", days: 30, source: "meter" });
        if (kind === "csv") {
          const url = URL.createObjectURL(new Blob(["﻿" + x.csv], { type: "text/csv;charset=utf-8" }));
          const a = document.createElement("a");
          a.href = url; a.download = x.filename || "napiecie_sieci.csv";
          root.appendChild(a); a.click(); a.remove();
          setTimeout(() => URL.revokeObjectURL(url), 5000);
        } else if (out) {
          out.innerHTML = `<textarea readonly style="width:100%;height:300px;margin-top:10px;font-size:12px">${esc(x.complaint)}</textarea>`;
        }
      } catch (e) {
        if (out) out.innerHTML = `<div class="hint">Nie udało się: ${esc((e && e.message) || e)}</div>`;
      }
    };
    const csv = root.querySelector("#vrep-csv"), txt = root.querySelector("#vrep-txt");
    if (csv) csv.addEventListener("click", () => run("csv"));
    if (txt) txt.addEventListener("click", () => run("txt"));
  }

  _updateLive() {
    if (this._view !== "overview" || !this._data) return;
    const root = this.shadowRoot;
    const valEl = root.querySelector("#live-val");
    const noteEl = root.querySelector("#live-note");
    const s = this._settings;
    if (valEl && (s.power_entity || this._hasPhases())) {
      const w = this._totalDrawW();
      if (w !== null) {
        valEl.innerHTML = Math.abs(w) >= 1000
          ? `${this._num(w / 1000, 2)}<small>kW</small>`
          : `${this._num(w, 0)}<small>W</small>`;
        valEl.className = `val ${w < 0 ? "down" : ""}`;
        const st = s.power_entity && this._stateNum(s.power_entity);
        const name = st ? (st.st.attributes.friendly_name || s.power_entity) : "suma faz L1–L3";
        if (noteEl) noteEl.textContent = `${w < 0 ? "oddawanie do sieci · " : ""}${name}`;
      } else {
        valEl.textContent = "—";
        if (noteEl) noteEl.textContent = `${s.power_entity || "moc faz"} — brak wartości`;
      }
    }
    const pfEl = root.querySelector("#pf-val");
    if (pfEl && s.pf_entity) {
      const v = this._pfVal(s.pf_entity);
      pfEl.innerHTML = `<b class="${v !== null && v >= 0.93 ? "down" : "up"}">${v !== null ? this._num(v, 2) : "—"}</b>`;
    }
    const tgEl = root.querySelector("#tg-live");
    if (tgEl) {
      const t = this._tanPhiLive();
      tgEl.innerHTML = t.v === null ? (t.why ? `<span class="muted">${t.why}</span>` : "—")
        : `<b class="${t.v > TAN_PHI_LIMIT || t.cap ? "up" : "down"}">${this._num(t.v, 2)}</b>${t.cap ? " · pojemnościowa" : ""}`;
      const from = root.querySelector("#tg-from");
      if (from) from.textContent = t.from ? `(${t.from})` : "";
    }
    const qEl = root.querySelector("#q-live");
    if (qEl && s.reactive_power_entity) {
      const q = this._base(s.reactive_power_entity);
      qEl.textContent = q === null ? "—" : Math.abs(q) >= 1000 ? `${this._num(q / 1000, 2)} kvar` : `${this._num(q, 0)} var`;
    }
    const ph = root.querySelector("#phases-body");
    if (ph) ph.innerHTML = this._phasesBody();
  }

  // ── charts (inline SVG) ──────────────────────────────────────────────
  _dailyChart(daily, tariff, W) {
    if (!daily.length) return `<div class="hint">Brak danych dziennych.</div>`;
    const zones = (tariff && tariff.zones) || ["flat"];
    const H = 220, padL = 34, padB = 22, padT = 8;
    const max = Math.max(...daily.map((d) => d.kwh), 0.1);
    const step = (W - padL) / daily.length;
    const bw = Math.min(48, Math.max(3, step * 0.7));
    const y = (v) => padT + (H - padT - padB) * (1 - v / max);
    let bars = "";
    daily.forEach((d, i) => {
      let acc = 0;
      const part = d.hours !== undefined && d.hours < 23; // eLicznik has not finished the day
      const x = padL + i * step + (step - bw) / 2;
      zones.forEach((z) => {
        const v = (d.zones && d.zones[z]) || 0;
        if (v <= 0) return;
        const y1 = y(acc + v), y0 = y(acc);
        bars += `<rect x="${x.toFixed(1)}" y="${y1.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(0.5, y0 - y1).toFixed(1)}" fill="${ZONE_COLORS[z] || "#00d4ff"}" opacity="${part ? 0.3 : d.free ? 0.55 : 0.9}"><title>${esc(d.date)} · ${esc(this._zoneLabel(z))}: ${this._num(v, 1)} kWh</title></rect>`;
        acc += v;
      });
      const dd = d.date.slice(8, 10);
      const every = Math.ceil(daily.length / Math.max(4, Math.floor(W / 34)));
      if (i % every === 0) {
        bars += `<text x="${(x + bw / 2).toFixed(1)}" y="${H - 6}" text-anchor="middle"${d.free ? ' style="fill:#a78bfa"' : ""}>${dd}</text>`;
      }
      bars += `<rect x="${x.toFixed(1)}" y="${padT}" width="${bw.toFixed(1)}" height="${H - padT - padB}" fill="transparent"><title>${esc(d.date)}${d.free ? " (dzień wolny)" : ""}${part ? ` (niepełny — ${d.hours} h danych)` : ""}: ${this._num(d.kwh, 1)} kWh${tariff && !tariff.needs_input ? " · " + this._zl(d.variable) : ""}${d.export_kwh ? ` · oddane ${this._num(d.export_kwh, 1)} kWh` : ""}</title></rect>`;
    });
    const grid = [0.5, 1].map((f) => `<line x1="${padL}" x2="${W}" y1="${y(max * f)}" y2="${y(max * f)}" stroke="rgba(255,255,255,0.06)"/><text x="${padL - 4}" y="${y(max * f) + 3}" text-anchor="end">${this._num(max * f, max * f < 10 ? 1 : 0)}</text>`).join("");
    const legend = zones.map((z) => `<span><i style="background:${ZONE_COLORS[z] || "#00d4ff"}"></i>${esc(this._zoneLabel(z))}</span>`).join("")
      + `<span><i style="background:#a78bfa"></i>dzień wolny (bledszy słupek)</span>`
      + (daily.some((d) => d.hours !== undefined && d.hours < 23) ? `<span><i style="background:#2ecc71;opacity:.3"></i>dzień niepełny (eLicznik jeszcze nie przysłał)</span>` : "")
      + `<span>kWh / dzień</span>`;
    return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" style="max-width:100%;display:block">${grid}${bars}</svg><div class="legend">${legend}</div>`;
  }

  _profileChart(profile, zoneByHour, tariff, W) {
    const work = profile.work || [], free = profile.free || [];
    const vals = [...work, ...free].filter((v) => v !== null && v !== undefined);
    if (!vals.length) return `<div class="hint">Profil pojawi się po kilku dniach z danymi.</div>`;
    const H = 200, padL = 34, padB = 22, padT = 8;
    const max = Math.max(...vals, 0.05) * 1.1;
    const step = (W - padL) / 24;
    const y = (v) => padT + (H - padT - padB) * (1 - v / max);
    let out = "";
    zoneByHour.forEach((z, h) => {
      out += `<rect x="${(padL + h * step).toFixed(1)}" y="${padT}" width="${step.toFixed(1)}" height="${H - padT - padB}" fill="${ZONE_COLORS[z] || "#00d4ff"}" opacity="0.07"/>`;
    });
    work.forEach((v, h) => {
      if (v === null || v === undefined) return;
      const x = padL + h * step + step * 0.18;
      out += `<rect x="${x.toFixed(1)}" y="${y(v).toFixed(1)}" width="${(step * 0.64).toFixed(1)}" height="${(H - padB - y(v)).toFixed(1)}" rx="2" fill="#00d4ff" opacity="0.85"><title>${h}:00 dzień roboczy: ${this._num(v * 1000, 0)} W</title></rect>`;
    });
    const pts = free.map((v, h) => v === null || v === undefined ? null : `${(padL + h * step + step / 2).toFixed(1)},${y(v).toFixed(1)}`).filter(Boolean);
    if (pts.length > 1) out += `<polyline points="${pts.join(" ")}" fill="none" stroke="#a78bfa" stroke-width="2"/>`;
    free.forEach((v, h) => {
      if (v === null || v === undefined) return;
      out += `<circle cx="${(padL + h * step + step / 2).toFixed(1)}" cy="${y(v).toFixed(1)}" r="3" fill="#a78bfa"><title>${h}:00 dzień wolny: ${this._num(v * 1000, 0)} W</title></circle>`;
    });
    for (let h = 0; h < 24; h += W < 520 ? 3 : 2) out += `<text x="${(padL + h * step + step / 2).toFixed(1)}" y="${H - 6}" text-anchor="middle">${h}</text>`;
    const grid = [0.5, 1].map((f) => `<line x1="${padL}" x2="${W}" y1="${y(max * f / 1.1)}" y2="${y(max * f / 1.1)}" stroke="rgba(255,255,255,0.06)"/><text x="${padL - 4}" y="${y(max * f / 1.1) + 3}" text-anchor="end">${this._num(max * f / 1.1 * 1000, 0)}</text>`).join("");
    const zones = (tariff && tariff.zones) || [];
    const legend = `<span><i style="background:#00d4ff"></i>dzień roboczy</span><span><i style="background:#a78bfa"></i>dzień wolny</span>`
      + zones.map((z) => `<span><i style="background:${ZONE_COLORS[z]};opacity:.45"></i>tło: ${esc(this._zoneLabel(z))}</span>`).join("") + `<span>W (średnio)</span>`;
    return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" style="max-width:100%;display:block">${grid}${out}</svg><div class="legend">${legend}</div>`;
  }

  // ── settings ────────────────────────────────────────────────────────
  _entityOptions(selected, filter) {
    const states = (this._hass && this._hass.states) || {};
    const ids = Object.keys(states).filter((id) => id.startsWith("sensor.") && filter(states[id])).sort();
    const opts = [`<option value="">— brak —</option>`].concat(ids.map((id) => {
      const name = states[id].attributes.friendly_name || id;
      return `<option value="${esc(id)}"${id === selected ? " selected" : ""}>${esc(name)} (${esc(id)})</option>`;
    }));
    if (selected && !states[selected]) opts.push(`<option value="${esc(selected)}" selected>${esc(selected)} (niedostępny)</option>`);
    return opts.join("");
  }

  _settingsHtml() {
    const d = this._data || {};
    const s = this._draft || JSON.parse(JSON.stringify(this._settings));
    this._draft = s;
    const catalog = (this._catalog && this._catalog.tariffs) || [];
    const t = catalog.find((x) => x.id === s.tariff) || catalog[0] || { zones: [], energy: {}, dist: {}, fixed: {} };
    const pr = s.prices || {};
    const val = (part, k) => (pr[part] && pr[part][k] !== undefined ? pr[part][k] : (t[part] || {})[k]);
    const opts = (raw, selected) => {
      const list = Array.isArray(raw) ? raw : [];
      const items = list.map((x) => `<option value="${esc(x.id)}"${x.id === selected ? " selected" : ""}>${esc(x.name)} (${esc(x.id)})</option>`);
      if (selected && !list.some((x) => x.id === selected)) items.push(`<option value="${esc(selected)}" selected>${esc(selected)} (brak statystyk)</option>`);
      return items.join("");
    };
    const sources = opts(d.sources, s.energy_stat);
    const zoneRows = (t.zones || []).map((z) => `
      <tr><td><span class="dot" style="display:inline-block;background:${ZONE_COLORS[z]}"></span> ${esc(this._zoneLabel(z))}</td>
      <td><input type="number" step="0.0001" min="0" data-part="energy" data-key="${z}" value="${esc(val("energy", z))}"></td>
      <td><input type="number" step="0.0001" min="0" data-part="dist" data-key="${z}" value="${esc(val("dist", z))}"></td></tr>`).join("");
    const fixedRows = Object.keys(t.fixed || {}).map((k) => `
      <label class="f">${esc(this._fixedLabel(k))} <span class="h">zł netto / miesiąc</span>
      <input type="number" step="0.01" min="0" data-part="fixed" data-key="${k}" value="${esc(val("fixed", k))}"></label>`).join("");
    const contractRate = pr.contract_rate !== undefined ? pr.contract_rate : t.contract_rate;
    const sumLine = (key, label) => (s[key] || []).length ? `
      <div class="f" style="grid-column:1/-1"><span>${label}</span>
        <span class="h">${(s[key] || []).map(esc).join(" + ")}</span>
        <button type="button" class="btn" data-clear="${key}" style="align-self:flex-start">Usuń sumę</button></div>` : "";
    const liveSelects = LIVE_ROLES.map(([role, label, kind, hint]) => `
      <label class="f">${esc(label)} <span class="h">${esc(hint)}</span>
        <select name="${role}">${this._entityOptions(s[role], KIND_FILTERS[kind])}</select></label>`).join("");
    return `<form id="f">
      <div class="grid cols" style="margin-top:0">
        <div class="card">
          <h3>Obiekt i licznik</h3>
          <div class="grid2">
            <label class="f">Typ obiektu
              <select name="site_kind">
                <option value="business"${s.site_kind === "business" ? " selected" : ""}>Firma (kwoty netto)</option>
                <option value="home"${s.site_kind === "home" ? " selected" : ""}>Dom (kwoty brutto)</option>
              </select></label>
            <label class="f">Taryfa
              <select name="tariff">${catalog.map((x) => `<option value="${x.id}"${x.id === s.tariff ? " selected" : ""}>${esc(x.label)}</option>`).join("")}</select></label>
            ${(s.import_stats || []).length ? sumLine("import_stats", "Energia pobrana — suma liczników faz") : `
            <label class="f" style="grid-column:1/-1">Energia pobrana z sieci (statystyka kWh)
              <span class="h">Tauron AMIplus: tauron_amiplus:…_consumption · Shelly: total active energy · Eastron: Total Import kWh</span>
              <select name="energy_stat"><option value="">automatycznie</option>${sources}</select></label>`}
            ${(s.export_stats || []).length ? sumLine("export_stats", "Energia oddana — suma liczników faz") : `
            <label class="f" style="grid-column:1/-1">Energia oddana do sieci (statystyka kWh) <span class="h">opcjonalnie — prosument, np. mikroinstalacja bez integracji falownika</span>
              <select name="export_stat"><option value="">— brak —</option>${opts(d.energy_all, s.export_stat)}</select></label>`}
            <label class="f" style="grid-column:1/-1">Energia bierna pobrana (statystyka kvarh) <span class="h">opcjonalnie — tg φ w miesiącu</span>
              <select name="reactive_energy_stat"><option value="">— brak —</option>${opts(d.reactive_stats, s.reactive_energy_stat)}</select></label>
            <label class="f" style="grid-column:1/-1"><span><input type="checkbox" name="balance_hourly" style="width:auto"${s.balance_hourly !== false ? " checked" : ""}>
              Koszty od poboru po bilansowaniu godzinowym (gdy jest eksport)</span></label>
            <label class="f">Moc umowna <span class="h">kW, z faktury</span>
              <input type="number" step="0.1" min="0" name="contract_kw" value="${esc(s.contract_kw ?? "")}"></label>
            <label class="f">Próg stałego obciążenia <span class="h">W — powyżej panel ostrzega</span>
              <input type="number" step="10" min="0" name="baseload_alert_w" value="${esc(s.baseload_alert_w ?? 150)}"></label>
          </div>
        </div>
        <div class="card">
          <h3>Licznik w rozdzielnicy · pomiar na żywo <small>opcjonalnie · tylko odczyt</small></h3>
          <div class="actions" style="justify-content:flex-start;margin:0 0 10px">
            <button type="button" class="btn" id="detect">🔍 Wykryj licznik</button>
            <span class="hint" style="margin:0">Shelly EM / 3EM / Pro 3EM, Eastron SDM (Modbus)${s.meter_device ? ` · teraz: <b>${esc(s.meter_device)}</b>` : ""}</span>
          </div>
          <div id="detect-out">${this._detectHtml()}</div>
          <div class="grid2">
            ${liveSelects}
            <label class="f">Znak mocy licznika <span class="h">Shelly i Eastron: pobór dodatni</span>
              <select name="power_sign">
                <option value="import_positive"${s.power_sign !== "export_positive" ? " selected" : ""}>pobór dodatni, oddawanie ujemne</option>
                <option value="export_positive"${s.power_sign === "export_positive" ? " selected" : ""}>oddawanie dodatnie, pobór ujemny</option>
              </select></label>
            <label class="f">Źródło napięć (raport, alert) <span class="h">bez falownika zawsze licznik</span>
              <select name="voltage_source">
                <option value="auto"${(s.voltage_source || "auto") === "auto" ? " selected" : ""}>automatycznie (falownik, gdy jest)</option>
                <option value="inverter"${s.voltage_source === "inverter" ? " selected" : ""}>falownik</option>
                <option value="meter"${s.voltage_source === "meter" ? " selected" : ""}>licznik</option>
              </select></label>
          </div>
        </div>
        <div class="card wide">
          <h3>Ceny z faktury <small>zł netto · domyślnie ${esc(t.label || "")}</small></h3>
          <table class="prices"><thead><tr><th>Strefa</th><th>Energia czynna (zł/kWh)</th><th>Dystrybucja — składnik zmienny (zł/kWh)</th></tr></thead>
            <tbody>${zoneRows}</tbody></table>
          <div class="hint">Do dystrybucji panel sam dolicza stawkę jakościową, OZE i kogeneracyjną
            (${this._num((this._catalog && this._catalog.per_kwh_fees) || 0.0435, 4)} zł/kWh). Cena energii w firmach zależy od umowy — przepisz ją z faktury.</div>
          <div class="grid2" style="margin-top:12px">
            ${fixedRows}
            ${t.contract_rate || s.site_kind === "business" ? `<label class="f">Stawka za moc umowną <span class="h">zł netto za kW / miesiąc</span>
              <input type="number" step="0.01" min="0" data-part="contract_rate" value="${esc(contractRate)}"></label>` : ""}
          </div>
          <div class="actions">
            <button type="button" class="btn" id="reset">Przywróć ceny domyślne</button>
            <button type="submit" class="btn primary">Zapisz</button>
          </div>
        </div>
      </div></form>`;
  }

  // Auto-detection result (smartinghome/meter/detect): proposals the user accepts row by row
  _detectHtml() {
    const r = this._detect;
    if (!r) return "";
    if (r.error) return `<div class="banner err">Wykrywanie nie powiodło się: ${esc(r.error)}</div>`;
    if (!(r.proposals || []).length) {
      return `<div class="banner warn">Nie znaleziono licznika w integracjach ${esc((r.platforms || []).join(", "))}
        (sprawdzono ${r.scanned || 0} encji). Wybierz encje ręcznie poniżej — każdy licznik z encjami HA się nada.</div>`;
    }
    const states = (this._hass && this._hass.states) || {};
    return r.proposals.map((p, i) => {
      const rows = Object.entries(p.mapping).map(([role, eid]) => {
        const ids = Array.isArray(eid) ? eid : [eid];
        const off = ids.some((x) => (p.disabled || []).includes(x));
        const live = ids.map((x) => states[x] ? `${states[x].state} ${states[x].attributes.unit_of_measurement || ""}` : "—").join(" + ");
        return `<tr><td><input type="checkbox" data-prop="${i}" data-role="${esc(role)}" checked style="width:auto"></td>
          <td>${esc(ROLE_LABELS[role] || role)}</td><td>${ids.map(esc).join("<br>")}${off ? ` <span class="up">(wyłączona w HA)</span>` : ""}</td>
          <td class="muted">${esc(live)}</td><td class="muted">${esc((p.reasons || {})[role] || "")}</td></tr>`;
      }).join("");
      const amb = Object.entries(p.ambiguous || {}).map(([role, ids]) =>
        `<div class="hint">${esc(ROLE_LABELS[role] || role)}: kilka pasujących encji (${ids.map(esc).join(", ")}) — wybierz ręcznie.</div>`).join("");
      return `<div class="banner" style="margin:0 0 10px">
        <b>${esc(p.device_name || p.platform)}</b> <span class="muted">· ${esc(p.platform)} · ${p.confidence === "exact" ? "dopasowanie dokładne" : "dopasowanie po nazwach — sprawdź"}</span>
        <table class="prices"><thead><tr><th></th><th>Rola</th><th>Encja</th><th>Teraz</th><th>Na podstawie</th></tr></thead><tbody>${rows}</tbody></table>
        ${amb}${(p.notes || []).map((n) => `<div class="hint">${esc(n)}</div>`).join("")}
        <div class="actions" style="justify-content:flex-start"><button type="button" class="btn primary" data-apply="${i}">Użyj zaznaczonych</button></div>
      </div>`;
    }).join("");
  }

  _bindSettings() {
    const f = this.shadowRoot.querySelector("#f");
    if (!f) return;
    const fields = ["site_kind", "tariff", "energy_stat", "export_stat", "reactive_energy_stat",
      "power_sign", "voltage_source", ...LIVE_ROLES.map((r) => r[0])];
    const read = () => {
      const s = this._draft;
      fields.forEach((n) => { if (f.elements[n]) s[n] = f.elements[n].value; });
      s.balance_hourly = !!(f.elements.balance_hourly && f.elements.balance_hourly.checked);
      const num = (v) => (v === "" || v === null ? null : Number(v));
      s.contract_kw = num(f.elements.contract_kw.value);
      s.baseload_alert_w = num(f.elements.baseload_alert_w.value);
      const prices = { energy: {}, dist: {}, fixed: {} };
      f.querySelectorAll("input[data-part]").forEach((i) => {
        if (i.value === "") return;
        if (i.dataset.part === "contract_rate") prices.contract_rate = Number(i.value);
        else prices[i.dataset.part][i.dataset.key] = Number(i.value);
      });
      s.prices = prices;
      return s;
    };
    f.elements.tariff.addEventListener("change", () => {
      read();
      this._draft.prices = {}; // new tariff → its own defaults
      this._renderBody();
    });
    f.elements.site_kind.addEventListener("change", () => { read(); this._renderBody(); });
    f.querySelector("#reset").addEventListener("click", () => { read(); this._draft.prices = {}; this._renderBody(); });
    f.querySelectorAll("[data-clear]").forEach((b) => b.addEventListener("click", () => {
      read();
      this._draft[b.dataset.clear] = [];
      this._renderBody();
    }));
    f.querySelector("#detect").addEventListener("click", async (ev) => {
      read();
      ev.target.disabled = true;
      ev.target.textContent = "Szukam…";
      try {
        this._detect = await this._hass.callWS({ type: "smartinghome/meter/detect" });
      } catch (e) {
        this._detect = { error: (e && (e.message || e.code)) || String(e) };
      }
      this._renderBody();
    });
    f.querySelectorAll("[data-apply]").forEach((b) => b.addEventListener("click", () => {
      const p = this._detect && this._detect.proposals[Number(b.dataset.apply)];
      if (!p) return;
      read();
      const s = this._draft;
      f.querySelectorAll(`input[data-prop="${b.dataset.apply}"]`).forEach((c) => {
        if (!c.checked) return;
        const role = c.dataset.role;
        s[role] = p.mapping[role];
        // A summed import / export replaces the single statistic and the other way round
        if (role === "import_stats") s.energy_stat = "";
        if (role === "export_stats") s.export_stat = "";
        if (role === "energy_stat") s.import_stats = [];
        if (role === "export_stat") s.export_stats = [];
      });
      s.meter_device = p.device_name || p.platform || "";
      this._detect = null;
      this._renderBody();
    }));
    f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const s = read();
      const btn = f.querySelector("button[type=submit]");
      btn.disabled = true;
      btn.textContent = "Zapisywanie…";
      try {
        await this._hass.callWS({ type: "smartinghome/settings/update", settings: { meter: s } });
        this._draft = null;
        this._detect = null;
        this._vrepTs = 0;
        this._view = "overview";
        await this._load();
        this.shadowRoot.querySelector(".wrap").scrollIntoView({ block: "start" });
      } catch (e) {
        btn.disabled = false;
        btn.textContent = "Zapisz";
        alert("Nie udało się zapisać: " + ((e && e.message) || e));
      }
    });
  }
}

if (!customElements.get("smartinghome-meter-panel")) {
  customElements.define("smartinghome-meter-panel", SmartingHomeMeterPanel);
}
