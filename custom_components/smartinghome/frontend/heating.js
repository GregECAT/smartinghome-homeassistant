// Smarting HOME — autonomous energy management for Home Assistant
// Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
// SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
// Commercial software, not open source: copying, porting, rewriting (also with AI
// tools), redistributing or removing this notice / the license check requires the
// Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
// SH-ID: 8b1aab899d5a
// Smarting HOME — "Ogrzewanie" panel: wood gasification boiler, heat buffer, hot water.
// Data: smartinghome/heating/status + …/save (heating_ha.py) and the HA history
// of the mapped probes. Read only: nothing in the boiler room is switched from here.

const REFRESH_MS = 10 * 1000;
const CHART_REFRESH_MS = 5 * 60 * 1000;
const CHART_HOURS = 24;
const CHART_SERIES = [
  ["boiler_out", "Kocioł", "#e74c3c"],
  ["buffer_top", "Bufor góra", "#f7b731"],
  ["buffer_bottom", "Bufor dół", "#00d4ff"],
  ["dhw", "CWU", "#a78bfa"],
  ["heating_supply", "Zasilanie CO", "#2ecc71"],
  ["room", "Pokój", "#e0e6ed"],
];
const LEVEL_CLASS = { critical: "err", warning: "warn", info: "" };
const LEVEL_ICON = { critical: "🔴", warning: "🟠", info: "🔵" };
const STATE_COLOR = {
  unknown: "#64748b", cold: "#64748b", idle: "#94a3b8", ignition: "#f7b731",
  burning: "#e67e22", burnout: "#c0846a", overheat: "#e74c3c",
};
const unitOf = (st) => st.attributes.unit_of_measurement || "";
const KIND_FILTERS = {
  temperature: (st) => st.entity_id.startsWith("sensor.") && (st.attributes.device_class === "temperature" || /°[CF]$/.test(unitOf(st))),
  humidity: (st) => st.entity_id.startsWith("sensor.") && (st.attributes.device_class === "humidity" || (unitOf(st) === "%" && /humid|wilgot/i.test(st.entity_id))),
  power: (st) => st.entity_id.startsWith("sensor.") && (st.attributes.device_class === "power" || /^k?W$/.test(unitOf(st))),
  switch: (st) => st.entity_id.startsWith("switch."),
  cover: (st) => st.entity_id.startsWith("cover."),
};
const ROLE_HINTS = {
  boiler_out: "wyjście z kotła na wejściu do Laddomatu",
  buffer_top: "tuleja ~80–90 % wysokości bufora",
  buffer_bottom: "tuleja ~15–20 % wysokości bufora",
  dhw: "tuleja ~2/3 wysokości zasobnika",
  heating_supply: "≥ 0,5 m za zaworem mieszającym i pompą",
  heating_return: "opcjonalnie — ΔT grzejników",
  room: "np. Shelly H&T w pokoju referencyjnym",
  room_humidity: "opcjonalnie",
  outdoor: "opcjonalnie — czujnik lub pogoda",
  pump_switch: "opcjonalnie — przekaźnik Shelly 1PM",
  pump_power: "opcjonalnie — moc z Shelly 1PM (wykrywa awarię pompy)",
  dhw_pump_switch: "opcjonalnie — przekaźnik pompy ładującej CWU (np. Shelly 1PM Mini)",
  dhw_pump_power: "opcjonalnie — moc pompy CWU (wykrywa awarię pompy)",
};

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v, d = 1) => (v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toLocaleString("pl-PL", { minimumFractionDigits: d, maximumFractionDigits: d }));
const fmtTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString("pl-PL", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const fmtHours = (h) => {
  if (h == null) return "—";
  const m = Math.round(h * 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
};

// temperature → colour (cold blue → hot red)
function tempColor(t) {
  if (t == null) return "#334155";
  const x = Math.min(Math.max((t - 25) / 65, 0), 1);
  const stops = [[0, [0, 150, 255]], [0.45, [46, 204, 113]], [0.7, [247, 183, 49]], [1, [231, 76, 60]]];
  for (let i = 1; i < stops.length; i++) {
    if (x <= stops[i][0]) {
      const [x0, c0] = stops[i - 1], [x1, c1] = stops[i];
      const f = (x - x0) / (x1 - x0);
      const c = c0.map((v, k) => Math.round(v + (c1[k] - v) * f));
      return `rgb(${c[0]},${c[1]},${c[2]})`;
    }
  }
  return "rgb(231,76,60)";
}

class SmartingHomeHeatingPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._data = null;
    this._error = "";
    this._view = "overview";
    this._draft = null;
    this._chart = null;
    this._chartTs = 0;
    this._saveMsg = "";
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._renderShell();
      this._load();
    } else if (this._view === "settings") {
      this._updateLiveProbes();
    }
  }

  set narrow(v) {
    this._narrow = v;
    const b = this.shadowRoot.querySelector(".menu-btn");
    if (b) b.classList.toggle("show", !!v);
  }

  connectedCallback() {
    this._timer = setInterval(() => { if (!document.hidden) this._load(); }, REFRESH_MS);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  async _load() {
    if (!this._hass) return;
    try {
      this._data = await this._hass.callWS({ type: "smartinghome/heating/status" });
      this._error = "";
    } catch (e) {
      this._error = e.message || e.code || String(e);
    }
    if (this._view === "overview") {
      if (Date.now() - this._chartTs > CHART_REFRESH_MS) this._loadChart();
      const focused = this.shadowRoot.activeElement;
      if (focused && focused.tagName === "INPUT") return;   // typing the wood weight — refresh later
      this._renderBody();
    } else if (!this._draft) {
      this._renderBody();
    } else {
      this._updateLiveProbes();
    }
  }

  async _loadChart() {
    const ents = (this._data && this._data.config && this._data.config.entities) || {};
    const ids = CHART_SERIES.map(([role]) => ents[role]).filter(Boolean);
    this._chartTs = Date.now();
    if (!ids.length) { this._chart = null; return; }
    const end = new Date();
    const start = new Date(end.getTime() - CHART_HOURS * 3600 * 1000);
    try {
      const res = await this._hass.callWS({
        type: "history/history_during_period",
        start_time: start.toISOString(), end_time: end.toISOString(),
        entity_ids: ids, minimal_response: true, no_attributes: true, significant_changes_only: false,
      });
      this._chart = { start: start.getTime() / 1000, end: end.getTime() / 1000, res };
    } catch (e) {
      this._chart = { error: e.message || String(e) };
    }
    if (this._view === "overview") this._renderBody();
  }

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
        h1 { font-size:17px; font-weight:700; background:linear-gradient(135deg,#f7b731,#e74c3c);
          -webkit-background-clip:text; -webkit-text-fill-color:transparent; white-space:nowrap; }
        .sub { font-size:11px; color:#8899aa; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        .hr { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
        .seg { display:flex; background:rgba(0,0,0,0.25); border-radius:8px; padding:2px; }
        .seg button, .btn { border:none; background:transparent; color:#8899aa; font:inherit; font-size:12px;
          padding:6px 11px; border-radius:6px; cursor:pointer; }
        .seg button.on { background:rgba(247,183,49,0.14); color:#f7b731; }
        .btn { background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.08); color:#cbd5e1; }
        .btn:hover { background:rgba(247,183,49,0.12); color:#f7b731; }
        .btn.primary { background:linear-gradient(135deg,#f7b731,#e67e22); color:#0a1628; font-weight:700; border:none; }
        .content { padding:18px 20px 40px; max-width:1400px; margin:0 auto; }
        .grid { display:grid; gap:14px; }
        .kpis { grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); }
        .cols { grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr)); margin-top:14px; }
        .card { background:rgba(20,30,48,0.45); border:1px solid rgba(255,255,255,0.07); border-radius:14px; padding:16px; min-width:0; }
        .card.wide { grid-column:1/-1; }
        .card h3 { font-size:13px; font-weight:600; color:#a0aec0; letter-spacing:.02em; margin-bottom:12px;
          display:flex; justify-content:space-between; align-items:baseline; gap:4px 8px; flex-wrap:wrap; }
        .card h3 small { font-weight:400; color:#64748b; font-size:11px; }
        .kpi .lbl { font-size:11px; color:#8899aa; text-transform:uppercase; letter-spacing:.06em; }
        .kpi .val { font-size:28px; font-weight:700; margin:6px 0 2px; font-variant-numeric:tabular-nums; }
        .kpi .val small { font-size:14px; font-weight:500; color:#8899aa; margin-left:3px; }
        .kpi .note { font-size:12px; color:#8899aa; line-height:1.45; }
        .kpi.warn { border-color:rgba(247,183,49,0.35); background:rgba(247,183,49,0.06); }
        .kpi.bad { border-color:rgba(231,76,60,0.4); background:rgba(231,76,60,0.08); }
        .banner { border-radius:12px; padding:12px 14px; font-size:13px; line-height:1.5; margin-bottom:12px;
          background:rgba(0,212,255,0.07); border:1px solid rgba(0,212,255,0.25); }
        .banner.warn { background:rgba(247,183,49,0.08); border-color:rgba(247,183,49,0.35); }
        .banner.err { background:rgba(231,76,60,0.1); border-color:rgba(231,76,60,0.45); }
        .banner b { color:#fff; }
        .banner .act { color:#cbd5e1; margin-top:4px; }
        .bar { height:10px; border-radius:5px; background:rgba(255,255,255,0.06); overflow:hidden; margin-top:8px; }
        .bar span { display:block; height:100%; border-radius:5px; }
        table { width:100%; border-collapse:collapse; font-size:12px; }
        th { text-align:left; color:#64748b; font-weight:500; padding:6px; border-bottom:1px solid rgba(255,255,255,0.06); }
        td { padding:6px; border-bottom:1px solid rgba(255,255,255,0.04); font-variant-numeric:tabular-nums; }
        tr.open td { color:#f7b731; }
        svg text { fill:#94a3b8; font-size:11px; font-family:inherit; }
        .legend { display:flex; gap:14px; flex-wrap:wrap; font-size:11px; color:#8899aa; margin-top:8px; }
        .legend i { display:inline-block; width:10px; height:3px; border-radius:2px; margin-right:5px; vertical-align:3px; }
        .hint { font-size:12px; color:#8899aa; line-height:1.55; margin-top:8px; }
        .hint b { color:#e0e6ed; }
        .muted { color:#64748b; }
        .foot { font-size:11px; color:#64748b; margin-top:18px; line-height:1.6; }
        form .grid2 { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; }
        label.f { display:flex; flex-direction:column; gap:5px; font-size:12px; color:#a0aec0; }
        label.f span.h { font-size:11px; color:#64748b; }
        label.chk { display:flex; gap:8px; align-items:center; font-size:13px; cursor:pointer; }
        input, select { padding:8px 10px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12);
          border-radius:8px; color:#fff; font:inherit; font-size:13px; width:100%; }
        input[type=checkbox] { width:auto; }
        select option { background:#1e293b; }
        .probe { display:grid; grid-template-columns:minmax(150px,1fr) minmax(200px,2fr) 110px 80px; gap:8px; align-items:center;
          padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.05); font-size:12px; }
        .probe .name b { display:block; color:#e0e6ed; font-size:13px; }
        .probe .name span { color:#64748b; font-size:11px; }
        .probe .live { font-variant-numeric:tabular-nums; text-align:right; font-size:14px; font-weight:600; }
        .probe .live.bad { color:#e74c3c; } .probe .live.none { color:#64748b; font-weight:400; }
        .probe.head { font-size:11px; color:#64748b; padding:0 0 6px; text-transform:uppercase; letter-spacing:.05em; }
        .actions { display:flex; gap:8px; justify-content:flex-end; margin-top:16px; flex-wrap:wrap; align-items:center; }
        .ok { color:#2ecc71; font-size:12px; }
        .err { color:#e74c3c; font-size:12px; }
        .seg.modes button { font-size:12px; padding:6px 10px; }
        .ctl { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-top:12px; }
        .ctl .lbl { font-size:11px; color:#8899aa; text-transform:uppercase; letter-spacing:.05em; }
        .ctl .v { font-size:22px; font-weight:700; font-variant-numeric:tabular-nums; margin-top:4px; }
        .ctl .v small { font-size:12px; color:#8899aa; font-weight:500; margin-left:3px; }
        .ctl .s { font-size:11px; color:#8899aa; margin-top:2px; line-height:1.4; }
        .btns { display:flex; gap:6px; flex-wrap:wrap; margin-top:12px; align-items:center; }
        .btn.sm { padding:5px 9px; font-size:12px; }
        .btn.warn { border-color:rgba(231,76,60,0.4); color:#fca5a5; }
        .pill { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; background:rgba(255,255,255,0.06); color:#cbd5e1; }
        .pill.on { background:rgba(46,204,113,0.15); color:#2ecc71; } .pill.hot { background:rgba(231,76,60,0.15); color:#e74c3c; }
        .lock { font-size:13px; color:#cbd5e1; line-height:1.6; }
        .lock b { color:#f7b731; }
        .logl { font-size:12px; line-height:1.55; max-height:260px; overflow:auto; }
        .logl div { padding:3px 0; border-bottom:1px solid rgba(255,255,255,0.04); }
        .logl span { color:#64748b; margin-right:8px; font-variant-numeric:tabular-nums; }
        details summary { cursor:pointer; color:#a0aec0; font-size:12px; margin-top:10px; }
        pre.code { white-space:pre-wrap; font-size:11px; background:rgba(0,0,0,0.3); padding:10px; border-radius:8px; max-height:300px; overflow:auto; margin-top:8px; }
        .inline { display:flex; gap:6px; align-items:center; margin-top:10px; }
        .inline input { width:90px; }
        @media (max-width:700px) {
          .header { padding:8px 12px; } .content { padding:12px 12px 32px; }
          .kpi .val { font-size:24px; }
          .probe { grid-template-columns:1fr 90px; }
          .probe select { grid-column:1/-1; } .probe .off { grid-column:1/-1; } .probe.head { display:none; }
        }
      </style>
      <div class="wrap">
        <div class="header">
          <div class="hl">
            <button class="menu-btn" title="Menu">☰</button>
            <div style="min-width:0">
              <h1>🔥 Ogrzewanie</h1>
              <div class="sub" id="sub">Kocioł, bufor ciepła i CWU · Smarting HOME</div>
            </div>
          </div>
          <div class="hr">
            <div class="seg" id="views">
              <button data-view="overview" class="on">Przegląd</button>
              <button data-view="settings">Ustawienia</button>
            </div>
            <button class="btn" id="refresh" title="Odśwież">⟳</button>
          </div>
        </div>
        <div class="content" id="body"><div class="banner">Wczytywanie…</div></div>
      </div>`;
    const root = this.shadowRoot;
    root.querySelector(".menu-btn").addEventListener("click", () =>
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })));
    root.querySelector(".menu-btn").classList.toggle("show", !!this._narrow);
    root.querySelectorAll("#views button").forEach((b) => b.addEventListener("click", () => {
      this._view = b.dataset.view;
      this._draft = null;
      this._saveMsg = "";
      root.querySelectorAll("#views button").forEach((x) => x.classList.toggle("on", x === b));
      if (this._view === "overview") this._chartTs = 0;
      this._load();
    }));
    root.querySelector("#refresh").addEventListener("click", () => { this._chartTs = 0; this._load(); });
    root.getElementById("body").addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]");
      if (b && !b.disabled) this._onAction(b.dataset.act, b.dataset);
    });
    root.getElementById("body").addEventListener("input", (e) => {
      if (e.target.id === "loadkg") this._loadKg = e.target.value;
    });
  }

  _renderBody() {
    const body = this.shadowRoot.getElementById("body");
    if (!body) return;
    if (this._error) {
      body.innerHTML = `<div class="banner err">Nie udało się pobrać danych modułu: <b>${esc(this._error)}</b></div>`;
      return;
    }
    if (!this._data) return;
    body.innerHTML = this._view === "settings" ? this._settingsHtml() : this._overviewHtml();
    if (this._view === "settings") this._bindSettings();
  }

  // ── overview ──────────────────────────────────────────────────────────────
  _overviewHtml() {
    const d = this._data;
    if (!d.enabled) {
      return `<div class="banner warn">Moduł ogrzewania jest wyłączony. Włącz go w zakładce <b>Ustawienia</b>.</div>`;
    }
    const st = d.status || {};
    const ents = d.config.entities || {};
    const configured = ["boiler_out", "buffer_top", "buffer_bottom"].some((r) => ents[r]);
    let html = "";
    if (!configured) {
      html += `<div class="banner warn">Nie przypisano jeszcze czujników. W zakładce <b>Ustawienia</b> wybierz sondy kotła,
        bufora i CWU (np. czujniki DS18B20 z Shelly Plus Add-on).</div>`;
    }
    for (const a of st.alarms || []) {
      html += `<div class="banner ${LEVEL_CLASS[a.level] || ""}">${LEVEL_ICON[a.level] || ""} <b>${esc(a.title)}</b><br>${esc(a.message)}
        ${a.action ? `<div class="act">→ ${esc(a.action)}</div>` : ""}</div>`;
    }
    html += `<div class="grid kpis">${this._kpis(st)}</div>`;
    if (this._msg) html += `<div class="banner ${this._msgErr ? "err" : ""}" style="margin-top:14px">${esc(this._msg)}</div>`;
    html += `<div class="grid cols">
      <div class="card">${this._controlHtml(st)}</div>
      <div class="card">${this._advisorHtml(st)}</div>
    </div>`;
    html += `<div class="grid cols">
      <div class="card"><h3>Kotłownia <small>na żywo</small></h3>${this._schematic(st)}</div>
      <div class="card"><h3>Ostatnie ${CHART_HOURS} h <small>°C</small></h3>${this._chartHtml()}</div>
      <div class="card wide"><h3>Palenia w kotle <small>sesja = jedno palenie; energia to przyrost ciepła w buforze i CWU (bez tego, co w tym czasie zużył dom)</small></h3>${this._sessionsHtml(st)}</div>
      <div class="card">${this._seasonHtml()}</div>
      <div class="card"><h3>Dziennik decyzji <small>sterowanie, bezpieczeństwo, doradca</small></h3>${this._logHtml()}</div>
    </div>`;
    html += `<div class="foot">Bezpieczeństwo kotła zapewniają termiczny zawór bezpieczeństwa na wężownicy schładzającej, termostaty kotła
      i Laddomat — działają bez prądu i bez Home Assistanta. Integracja może tylko odebrać ciepło (zrzut do grzejników, pompa CO),
      nigdy go nie blokuje; w trybie „Wyłączone” niczego nie przełącza. Pompą Laddomatu i wentylatorem kotła steruje wyłącznie kocioł.</div>`;
    return html;
  }

  _kpis(st) {
    const b = st.boiler || {}, buf = st.buffer, dhw = st.dhw, probes = st.probes || {};
    const out = [];
    const trend = b.trend_k_min;
    const arrow = trend == null ? "" : trend > 0.2 ? " ↑" : trend < -0.2 ? " ↓" : " →";
    const bCls = b.state === "overheat" ? "bad" : (b.temp_c != null && b.temp_c >= (this._data.config.warn_c || 88)) ? "warn" : "";
    out.push(`<div class="card kpi ${bCls}"><div class="lbl">Kocioł</div>
      <div class="val" style="color:${STATE_COLOR[b.state] || "#e0e6ed"}">${num(b.temp_c)}<small>°C${arrow}</small></div>
      <div class="note"><b style="color:${STATE_COLOR[b.state] || "#e0e6ed"}">${esc(b.label || "—")}</b>${trend != null ? ` · ${num(trend, 2)} K/min` : ""}${b.since ? `<br>od ${fmtTime(b.since)}` : ""}</div></div>`);
    if (buf) {
      const pct = buf.charge_pct ?? 0;
      out.push(`<div class="card kpi ${pct >= 95 ? "warn" : ""}"><div class="lbl">Bufor</div>
        <div class="val">${num(buf.charge_pct, 0)}<small>%</small></div>
        <div class="note">${num(buf.energy_kwh)} z ${num(buf.capacity_kwh, 0)} kWh · średnio ${num(buf.mean_c)} °C${buf.estimated ? "<br>jedna sonda — szacunek" : ""}</div>
        <div class="bar"><span style="width:${Math.min(Math.max(pct, 0), 100)}%; background:linear-gradient(90deg,#00d4ff,#f7b731,#e74c3c)"></span></div></div>`);
      out.push(`<div class="card kpi"><div class="lbl">Bufor przyjmie jeszcze</div>
        <div class="val">${num(buf.wood_max_kg, 0)}<small>kg drewna</small></div>
        <div class="note">${num(buf.space_kwh)} kWh miejsca · ${num(buf.kwh_per_kg, 1)} kWh/kg w wodzie<br>bez odbioru przez dom — przy grzaniu domu można więcej</div></div>`);
    }
    if (st.runtime_h != null || st.discharge_kw != null) {
      out.push(`<div class="card kpi"><div class="lbl">Bufor wystarczy na</div>
        <div class="val">${st.runtime_h != null ? fmtHours(st.runtime_h) : "—"}</div>
        <div class="note">${st.discharge_kw != null ? `odbiór ${num(st.discharge_kw, 2)} kW (średnia z ostatniej godziny)` : ""}
        ${st.runtime_h != null ? `<br>do ok. ${new Date(Date.now() + st.runtime_h * 3600e3).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" })}` : ""}</div></div>`);
    }
    if (dhw) {
      out.push(`<div class="card kpi"><div class="lbl">CWU</div>
        <div class="val">${num(dhw.temp_c)}<small>°C</small></div>
        <div class="note">${num(dhw.energy_kwh)} kWh · ${num(dhw.charge_pct, 0)}% do ${num(this._data.config.dhw_target_c, 0)} °C</div></div>`);
    }
    const room = probes.room || {};
    if (room.value != null || st.humidity != null) {
      out.push(`<div class="card kpi"><div class="lbl">Pokój</div>
        <div class="val">${num(room.value)}<small>°C</small></div>
        <div class="note">zadana ${num(this._data.config.room_target_c)} °C${st.humidity != null ? ` · wilgotność ${num(st.humidity, 0)}%` : ""}</div></div>`);
    }
    return out.join("");
  }

  _schematic(st) {
    const p = st.probes || {}, b = st.boiler || {};
    const v = (r) => (p[r] && p[r].value != null ? p[r].value : null);
    const lbl = (r) => (v(r) == null ? (p[r] && p[r].status !== "missing" ? "⚠" : "—") : `${num(v(r))}°`);
    const top = v("buffer_top"), bot = v("buffer_bottom");
    const fire = STATE_COLOR[b.state] || "#64748b";
    const pump = st.pump || {}, dpump = st.dhw_pump || {};
    const pumpTxt = pump.on == null ? "" : pump.on ? `pompa CO ON${pump.power_w != null ? ` · ${num(pump.power_w, 0)} W` : ""}` : "pompa CO OFF";
    const dhwTxt = dpump.on == null ? "" : dpump.on ? "pompa CWU ON" : "pompa CWU OFF";
    const dhwW = dpump.on && dpump.power_w != null ? `${num(dpump.power_w, 0)} W` : "";
    const fromBoiler = this._data.config.dhw_source === "boiler_out";
    const dhwFeed = fromBoiler ? "M90 170 H105 V215 H290" : "M255 150 H275 V200 H290";
    const dhwFeedT = fromBoiler ? v("boiler_out") : top;
    return `<svg viewBox="0 0 440 230" width="100%" role="img" aria-label="Schemat kotłowni">
      <defs>
        <linearGradient id="gbuf" x1="0" y1="1" x2="0" y2="0">
          <stop offset="0%" stop-color="${tempColor(bot ?? top)}"/><stop offset="100%" stop-color="${tempColor(top ?? bot)}"/>
        </linearGradient>
      </defs>
      <!-- boiler -->
      <rect x="10" y="60" width="80" height="120" rx="10" fill="rgba(255,255,255,0.04)" stroke="${fire}" stroke-width="2"/>
      <text x="50" y="80" text-anchor="middle" style="fill:#e0e6ed;font-weight:600">Kocioł</text>
      <text x="50" y="120" text-anchor="middle" style="fill:${fire};font-size:20px;font-weight:700">${lbl("boiler_out")}</text>
      <text x="50" y="150" text-anchor="middle" style="fill:${fire}">${esc(b.label || "")}</text>
      ${b.firing ? `<text x="50" y="172" text-anchor="middle" style="font-size:16px">🔥</text>` : ""}
      <!-- boiler → buffer -->
      <path d="M90 85 H150 V70 H175" stroke="${tempColor(v("boiler_out"))}" stroke-width="4" fill="none"/>
      <path d="M175 170 H120 V160 H90" stroke="${tempColor(bot)}" stroke-width="4" fill="none" opacity="0.7"/>
      <rect x="118" y="77" width="34" height="18" rx="4" fill="#0f172a" stroke="#334155"/>
      <text x="135" y="90" text-anchor="middle" style="font-size:9px">Ladd.</text>
      <!-- buffer -->
      <rect x="175" y="30" width="80" height="170" rx="16" fill="url(#gbuf)" opacity="0.85" stroke="#475569"/>
      <text x="215" y="22" text-anchor="middle" style="fill:#e0e6ed;font-weight:600">Bufor</text>
      <text x="215" y="62" text-anchor="middle" style="fill:#0a1628;font-weight:700;font-size:14px">${lbl("buffer_top")}</text>
      <text x="215" y="185" text-anchor="middle" style="fill:#0a1628;font-weight:700;font-size:14px">${lbl("buffer_bottom")}</text>
      ${st.buffer ? `<text x="215" y="122" text-anchor="middle" style="fill:#0a1628;font-weight:700;font-size:16px">${num(st.buffer.charge_pct, 0)}%</text>` : ""}
      <!-- buffer → house -->
      <path d="M255 60 H300 V95 H330" stroke="${tempColor(v("heating_supply") ?? top)}" stroke-width="4" fill="none"/>
      <circle cx="300" cy="78" r="9" fill="#0f172a" stroke="#94a3b8"/><text x="300" y="82" text-anchor="middle" style="font-size:9px">M</text>
      <text x="300" y="112" text-anchor="middle" style="font-size:10px">${v("heating_supply") != null ? `zas. ${lbl("heating_supply")}` : ""}</text>
      <text x="300" y="126" text-anchor="middle" style="font-size:10px">${v("heating_return") != null ? `pow. ${lbl("heating_return")}` : ""}</text>
      <path d="M330 95 L380 60 L430 95 V150 H330 Z" fill="rgba(255,255,255,0.04)" stroke="#475569"/>
      <text x="380" y="118" text-anchor="middle" style="fill:#e0e6ed;font-size:15px;font-weight:700">${lbl("room")}</text>
      <text x="380" y="138" text-anchor="middle" style="font-size:10px">${st.humidity != null ? `${num(st.humidity, 0)}% RH` : "dom"}</text>
      <text x="380" y="168" text-anchor="middle" style="font-size:10px">${esc(pumpTxt)}</text>
      <!-- DHW -->
      <rect x="290" y="178" width="70" height="44" rx="10" fill="${tempColor(v("dhw"))}" opacity="0.8" stroke="#475569"/>
      <text x="325" y="198" text-anchor="middle" style="fill:#0a1628;font-weight:700">CWU</text>
      <text x="325" y="214" text-anchor="middle" style="fill:#0a1628;font-weight:700">${lbl("dhw")}</text>
      <path d="${dhwFeed}" stroke="${tempColor(dhwFeedT)}" stroke-width="${dpump.on ? 4 : 2}" fill="none" ${dpump.on ? "" : 'stroke-dasharray="4 3"'} opacity="${dpump.on ? 1 : 0.6}"/>
      <text x="400" y="198" text-anchor="middle" style="font-size:10px">${esc(dhwTxt)}</text>
      <text x="400" y="212" text-anchor="middle" style="font-size:10px">${esc(dhwW)}</text>
      ${v("outdoor") != null ? `<text x="430" y="20" text-anchor="end">na zewnątrz ${lbl("outdoor")}</text>` : ""}
    </svg>`;
  }

  _chartHtml() {
    const c = this._chart;
    if (!c) return `<div class="muted" style="font-size:12px">Brak przypisanych czujników albo historii.</div>`;
    if (c.error) return `<div class="muted" style="font-size:12px">Historia niedostępna: ${esc(c.error)}</div>`;
    const ents = this._data.config.entities || {}, offs = this._data.config.offsets || {};
    const W = 440, H = 200, L = 30, R = 6, T = 8, B = 20;
    const series = [];
    let lo = Infinity, hi = -Infinity;
    for (const [role, name, color] of CHART_SERIES) {
      const id = ents[role];
      const rows = id && c.res ? c.res[id] : null;
      if (!rows || !rows.length) continue;
      const pts = [];
      for (const r of rows) {
        const val = parseFloat(r.s ?? r.state);
        const ts = r.lu ?? r.lc ?? (r.last_updated ? Date.parse(r.last_updated) / 1000 : null);
        if (!Number.isFinite(val) || ts == null || val <= -55 || val >= 125) continue;
        const v = val + (Number(offs[role]) || 0);
        pts.push([Math.max(ts, c.start), v]);
        lo = Math.min(lo, v); hi = Math.max(hi, v);
      }
      if (pts.length) { pts.push([c.end, pts[pts.length - 1][1]]); series.push({ name, color, pts }); }
    }
    if (!series.length) return `<div class="muted" style="font-size:12px">Brak danych w historii.</div>`;
    lo = Math.floor((lo - 2) / 10) * 10; hi = Math.ceil((hi + 2) / 10) * 10;
    const x = (ts) => L + (ts - c.start) / (c.end - c.start) * (W - L - R);
    const y = (v) => T + (hi - v) / (hi - lo) * (H - T - B);
    let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Temperatury z ostatniej doby">`;
    for (let g = lo; g <= hi; g += 10) {
      svg += `<line x1="${L}" x2="${W - R}" y1="${y(g)}" y2="${y(g)}" stroke="rgba(255,255,255,0.06)"/><text x="${L - 4}" y="${y(g) + 3}" text-anchor="end">${g}</text>`;
    }
    for (let h = 0; h <= CHART_HOURS; h += 6) {
      const ts = c.start + h * 3600;
      const t = new Date(ts * 1000).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
      const anchor = h === 0 ? "start" : h === CHART_HOURS ? "end" : "middle";
      svg += `<text x="${x(ts)}" y="${H - 5}" text-anchor="${anchor}">${t}</text>`;
    }
    for (const s of series) {
      // step line: a value holds until the next report
      let d = "";
      s.pts.forEach(([ts, v], i) => {
        d += i === 0 ? `M${x(ts).toFixed(1)} ${y(v).toFixed(1)}` : ` H${x(ts).toFixed(1)} V${y(v).toFixed(1)}`;
      });
      svg += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="1.6"/>`;
    }
    svg += "</svg>";
    const legend = series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("");
    return svg + `<div class="legend">${legend}</div>`;
  }

  _controlHtml(st) {
    const d = this._data, c = d.config, ctl = st.control || {}, admin = d.admin;
    const modes = d.modes || {};
    const modeBtns = Object.entries(modes).map(([k, label]) =>
      `<button data-act="mode" data-mode="${k}" class="${c.mode === k ? "on" : ""}" ${admin ? "" : "disabled"} title="${esc(label)}">${esc({ off: "Wyłączone", manual: "Ręczne", auto: "Auto", summer: "Lato" }[k] || label)}</button>`).join("");
    const v = ctl.valve || {}, pump = ctl.pump || {}, dpump = ctl.dhw_pump || {};
    const supply = ((st.probes || {}).heating_supply || {}).value;
    let html = `<h3>Sterowanie <small>${esc(ctl.mode_label || "")}</small></h3>
      <div class="seg modes">${modeBtns}</div>`;
    if (ctl.dump) html += `<div class="banner err" style="margin-top:12px">⚠️ <b>Zrzut ciepła do domu</b> — kocioł przegrzany: zawór otwarty, pompa CO włączona.</div>`;
    if (ctl.limiter) html += `<div class="banner warn" style="margin-top:12px">Ogranicznik: zasilanie powyżej ${num(c.supply_max_c, 0)} °C — zawór jest przymykany.</div>`;
    const corr = ctl.room_corr ? ` ${ctl.room_corr > 0 ? "+" : ""}${num(ctl.room_corr)} K pokój` : "";
    html += `<div class="ctl">
      <div><div class="lbl">Zasilanie zadane</div><div class="v">${num(ctl.supply_target)}<small>°C</small></div>
        <div class="s">krzywa ${num(ctl.curve_c)} °C${corr}${ctl.note ? `<br>${esc(ctl.note)}` : ""}</div></div>
      <div><div class="lbl">Zasilanie</div><div class="v">${num(supply)}<small>°C</small></div>
        <div class="s">${ctl.heating_needed ? "dom grzany" : c.mode === "auto" ? "bez zapotrzebowania" : ""}</div></div>
      <div><div class="lbl">Na zewnątrz</div><div class="v">${num(ctl.t_out)}<small>°C</small></div>
        <div class="s">${ctl.t_out_source ? `z: ${esc(ctl.t_out_source)}` : "brak danych"}${ctl.t_out_damped != null ? ` · średnio ${num(ctl.t_out_damped)} °C` : ""}</div></div>
      <div><div class="lbl">Pokój zadana</div><div class="v">${num(ctl.room_target)}<small>°C</small></div>
        <div class="s">${ctl.night ? "obniżenie nocne" : ""}${admin ? ` <button class="btn sm" data-act="room" data-delta="-0.5">−</button> <button class="btn sm" data-act="room" data-delta="0.5">+</button>` : ""}</div></div>
    </div>`;
    if (v.configured) {
      const pos = v.position ?? 0;
      html += `<div style="margin-top:14px; font-size:12px; color:#a0aec0">Zawór mieszający: <b style="color:#e0e6ed">${num(pos, 0)}%</b>
        ${v.known ? "(szacunek z czasu pracy)" : '<span class="pill hot">nieskalibrowany</span>'}
        ${v.saturated === "open" ? '<span class="pill hot">w pełni otwarty — bufor za zimny</span>' : v.saturated === "close" ? '<span class="pill">zamknięty</span>' : ""}
        ${v.busy ? '<span class="pill on">w ruchu</span>' : ""}
        <div class="bar"><span style="width:${Math.min(Math.max(pos, 0), 100)}%; background:linear-gradient(90deg,#00d4ff,#e67e22)"></span></div></div>`;
    } else {
      html += `<div class="hint">Siłownik zaworu nie jest skonfigurowany — zadana temperatura zasilania jest podpowiedzią do ręcznego ustawienia zaworu.</div>`;
    }
    const pill = (on) => (on == null ? '<span class="pill">brak odczytu</span>' : on ? '<span class="pill on">pracuje</span>' : '<span class="pill">stoi</span>');
    if (pump.configured) {
      html += `<div style="margin-top:10px; font-size:12px; color:#a0aec0">Pompa CO: ${pill(pump.on)}</div>`;
    }
    if (dpump.configured) {
      const auto = c.mode === "auto" || c.mode === "summer";
      html += `<div style="margin-top:6px; font-size:12px; color:#a0aec0">Pompa CWU: ${pill(dpump.on)}
        ${dpump.dump ? '<span class="pill hot">zrzut ciepła do CWU</span>' : ""}
        ${dpump.reason && c.mode !== "off" ? `<span class="muted">· ${auto || dpump.dump ? "" : "w trybie Auto: "}${esc(dpump.reason)}</span>` : ""}</div>`;
    }
    if (admin && c.mode !== "off" && (v.configured || pump.configured || dpump.configured)) {
      const manual = c.mode === "manual";
      html += `<div class="btns">
        ${v.configured && manual ? `<button class="btn sm" data-act="cmd" data-cmd="valve_close" data-seconds="5">◀ Zimniej 5 s</button>
          <button class="btn sm" data-act="cmd" data-cmd="valve_open" data-seconds="5">Cieplej 5 s ▶</button>
          <button class="btn sm" data-act="cmd" data-cmd="valve_stop">■ Stop</button>` : ""}
        ${v.configured ? `<button class="btn sm" data-act="cmd" data-cmd="valve_home" title="Dojazd do pełnego zamknięcia (ok. ${num(c.valve_run_s, 0)} s)">Kalibruj zawór</button>` : ""}
        ${pump.configured && manual ? `<button class="btn sm" data-act="cmd" data-cmd="pump_on">Pompa CO ON</button><button class="btn sm" data-act="cmd" data-cmd="pump_off">Pompa CO OFF</button>` : ""}
        ${dpump.configured && manual ? `<button class="btn sm" data-act="cmd" data-cmd="dhw_pump_on">Pompa CWU ON</button><button class="btn sm" data-act="cmd" data-cmd="dhw_pump_off">Pompa CWU OFF</button>` : ""}
      </div>${manual ? "" : '<div class="hint">Przyciski zaworu i pomp działają w trybie „Ręczne”.</div>'}`;
    }
    return html;
  }

  _advisorHtml(st) {
    const a = st.advice || {}, d = this._data;
    let html = `<h3>Doradca palenia <small>PRO</small></h3>`;
    if (a.locked) {
      return html + `<div class="lock"><b>Funkcja PRO.</b> Doradca uczy się strat ciepła domu z danych bufora i prognozy pogody,
        podpowiada <b>do której rozpalić</b> i <b>ile drewna załadować</b>, żeby nie przegrzać bufora, oraz liczy koszt sezonu.
        Monitoring, alarmy bezpieczeństwa i sterowanie krzywą pogodową działają bez licencji.</div>`;
    }
    const b = a.building || {};
    const basis = a.basis === "model" ? "z modelu domu i prognozy pogody" : a.basis === "trend" ? "z odbioru w ostatniej godzinie" : "";
    html += `<div class="ctl">
      <div><div class="lbl">Bufor wystarczy do</div><div class="v">${a.empty_at ? fmtTime(a.empty_at) : "—"}</div><div class="s">${esc(basis)}</div></div>
      <div><div class="lbl">Rozpal do</div><div class="v" style="color:#f7b731">${a.firing ? "pali się" : a.light_at ? fmtTime(a.light_at) : "—"}</div>
        <div class="s">${a.firing ? "" : `z wyprzedzeniem ${num(d.config.light_lead_h, 1)} h`}</div></div>
      <div><div class="lbl">Załaduj</div><div class="v">${a.wood_kg != null ? num(a.wood_kg, 0) : "—"}<small>kg</small></div>
        <div class="s">${a.fill_kwh != null ? `${num(a.fill_kwh)} kWh: bufor, CWU i dom przez ${num(d.config.burn_hours, 0)} h` : ""}<br>${num(a.per_kg, 2)} kWh/kg${a.learned_per_kg ? " (z ważenia)" : " (z ustawień)"}</div></div>
      <div><div class="lbl">Dom potrzebuje</div><div class="v">${a.demand_24h_kwh != null ? num(a.demand_24h_kwh, 0) : "—"}<small>kWh / 24 h</small></div>
        <div class="s">${a.demand_kw != null ? `teraz ${num(a.demand_kw, 2)} kW` : ""}</div></div>
    </div>
    <div class="hint">Model domu: ${b.ua_w_k != null ? `<b>${num(b.ua_w_k, 0)} W/K</b> strat (R² ${b.r2 != null ? num(b.r2, 2) : "—"}, ${b.samples} h danych)`
      : `uczy się — ${b.samples || 0} z min. 24 godzin bez palenia${d.weather_entities && d.weather_entities.length ? "" : "; brak encji pogody w HA"}`}.
      ${a.forecast_hours ? `Prognoza: ${a.forecast_hours} h.` : ""}</div>`;
    if (d.admin) {
      html += `<div class="inline"><input id="loadkg" type="number" min="0.5" max="200" step="0.5" placeholder="kg" value="${esc(this._loadKg || "")}">
        <button class="btn sm" data-act="load">Zapisz zważony załadunek</button></div>
        <div class="hint">Zważ 2–3 załadunki — doradca policzy, ile ciepła daje kilogram Twojego drewna w Twoim kotle.</div>`;
    }
    return html;
  }

  _seasonHtml() {
    const rows = (this._data.monthly || []).slice().reverse();
    let html = `<h3>Sezon grzewczy <small>z zarejestrowanych paleń</small></h3>`;
    if (!rows.length) return html + `<div class="muted" style="font-size:12px">Brak paleń w historii.</div>`;
    const price = this._data.config.wood_price_kg;
    const name = (m) => new Date(`${m}-01T12:00:00`).toLocaleDateString("pl-PL", { month: "long", year: "numeric" });
    html += `<div style="overflow-x:auto"><table><thead><tr><th>Miesiąc</th><th>Palenia</th><th>Godziny</th><th>Do bufora</th><th>Drewno</th>${price ? "<th>Koszt</th>" : ""}</tr></thead><tbody>`;
    for (const m of rows) {
      html += `<tr><td>${esc(name(m.month))}</td><td>${m.fires}${m.overheats ? ` <span class="pill hot">⚠ ${m.overheats}</span>` : ""}</td><td>${num(m.hours, 0)}</td>
        <td>${num(m.stored_kwh, 0)} kWh</td><td>~${num(m.wood_kg, 0)} kg${m.weighed ? ` <span class="muted">(${m.weighed} ważone)</span>` : ""}</td>
        ${price ? `<td>${m.cost != null ? `${num(m.cost, 0)} zł` : "—"}</td>` : ""}</tr>`;
    }
    html += `</tbody></table></div>`;
    if (!price) html += `<div class="hint">Podaj cenę drewna (zł/kg) w ustawieniach, a pojawi się koszt.</div>`;
    return html;
  }

  _logHtml() {
    const log = (this._data.log || []).slice().reverse().slice(0, 40);
    if (!log.length) return `<div class="muted" style="font-size:12px">Jeszcze nic się nie wydarzyło.</div>`;
    return `<div class="logl">${log.map((e) => `<div><span>${fmtTime(e.ts)}</span>${esc(e.text)}</div>`).join("")}</div>`;
  }

  async _onAction(act, ds) {
    this._msg = ""; this._msgErr = false;
    try {
      if (act === "mode") {
        this._data = await this._hass.callWS({ type: "smartinghome/heating/save", config: { mode: ds.mode } });
      } else if (act === "room") {
        const cur = Number(this._data.config.room_target_c) || 21;
        const next = Math.min(Math.max(Math.round((cur + Number(ds.delta)) * 2) / 2, 10), 28);
        this._data = await this._hass.callWS({ type: "smartinghome/heating/save", config: { room_target_c: next } });
      } else if (act === "cmd") {
        const msg = { type: "smartinghome/heating/command", action: ds.cmd };
        if (ds.seconds) msg.seconds = Number(ds.seconds);
        this._data = await this._hass.callWS(msg);
      } else if (act === "load") {
        const el = this.shadowRoot.getElementById("loadkg");
        const kg = parseFloat(el && el.value);
        this._data = await this._hass.callWS({ type: "smartinghome/heating/command", action: "log_load", kg });
        this._loadKg = "";
        this._msg = `Zapisano załadunek ${num(kg, 1)} kg.`;
      } else if (act === "guard") {
        if (ds.guard === "remove" && !confirm("Usunąć skrypt bezpieczeństwa z Shelly pompy?")) return;
        this._guard = { loading: true };
        this._renderBody();
        this._guard = await this._hass.callWS({ type: "smartinghome/heating/guard", action: ds.guard });
      } else if (act === "copy-guard") {
        await navigator.clipboard.writeText((this._guard && this._guard.code) || "");
        this._msg = "Skopiowano kod skryptu.";
      }
    } catch (e) {
      this._msg = e.message || e.code || String(e);
      this._msgErr = true;
      if (act === "guard") this._guard = { error: this._msg };
    }
    if (this._view === "settings" && act !== "guard" && act !== "copy-guard") this._draft = null;
    this._renderBody();
  }

  _sessionsHtml(st) {
    const rows = [];
    const s = st.session;
    if (s) {
      rows.push(`<tr class="open"><td>${fmtTime(s.start)}</td><td>trwa · ${fmtHours(s.duration_h)}</td><td>${num(s.max_c, 0)} °C</td>
        <td>${num(s.stored_kwh)} kWh</td><td>—</td><td>${s.overheat ? "⚠ przegrzanie" : ""}</td></tr>`);
    }
    for (const x of (this._data.sessions || []).slice().reverse()) {
      rows.push(`<tr><td>${fmtTime(x.start)}</td><td>${fmtHours(x.duration_h)}</td><td>${num(x.max_c, 0)} °C</td>
        <td>${num(x.stored_kwh)} kWh</td><td>${x.wood_est_kg != null ? `~${num(x.wood_est_kg, 0)} kg` : "—"}</td>
        <td>${x.overheat ? "⚠ przegrzanie" : ""}</td></tr>`);
    }
    if (!rows.length) return `<div class="muted" style="font-size:12px">Jeszcze nie zarejestrowano palenia. Sesja zaczyna się, gdy temperatura kotła rośnie, a kończy, gdy kocioł wygaśnie.</div>`;
    return `<div style="overflow-x:auto"><table><thead><tr><th>Start</th><th>Czas</th><th>Maks.</th><th>Do bufora i CWU</th><th>Drewno (szac.)</th><th></th></tr></thead>
      <tbody>${rows.join("")}</tbody></table></div>`;
  }

  // ── settings ──────────────────────────────────────────────────────────────
  _settingsHtml() {
    const d = this._data;
    const admin = d.admin;
    const c = this._draft || (this._draft = JSON.parse(JSON.stringify(d.config)));
    const dis = admin ? "" : "disabled";
    const prof = (d.profile_defaults || {})[c.profile] || {};
    const sel = (name, options, value) => `<select name="${name}" ${dis}>${Object.entries(options)
      .map(([k, label]) => `<option value="${esc(k)}" ${k === value ? "selected" : ""}>${esc(label)}</option>`).join("")}</select>`;
    const field = (name, label, hint, value, attrs = "", placeholder = "") =>
      `<label class="f">${esc(label)}<input name="${name}" type="number" ${attrs} value="${value ?? ""}" placeholder="${esc(placeholder)}" ${dis}>${hint ? `<span class="h">${esc(hint)}</span>` : ""}</label>`;
    const stored = d.config;   // what is in effect (defaults resolved)
    let html = "";
    if (!admin) html += `<div class="banner warn">Ustawienia ogrzewania zmienia tylko administrator Home Assistant.</div>`;
    html += `<form id="f" onsubmit="return false">`;
    html += `<div class="card"><h3>Moduł</h3>
      <div class="grid2" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px">
        <label class="chk"><input type="checkbox" name="enabled" ${c.enabled ? "checked" : ""} ${dis}> Moduł ogrzewania włączony</label>
        <label class="chk"><input type="checkbox" name="notify" ${c.notify !== false ? "checked" : ""} ${dis}> Powiadomienia o alarmach (kanały z panelu Smarting HOME)</label>
        <label class="f">Kocioł${sel("profile", d.profiles || {}, c.profile)}</label>
        <label class="f">Odbiorniki ciepła${sel("emitters", d.emitters || {}, c.emitters)}</label>
      </div>
      <div class="hint">Alarmy krytyczne (przegrzanie, utrata czujnika w trakcie palenia) zawsze trafiają też do powiadomień Home Assistanta.</div></div>`;

    html += `<div class="card" style="margin-top:14px"><h3>Czujniki <small>na żywo — ogrzej sondę w dłoni, a jej wartość zacznie rosnąć</small></h3>
      <div class="probe head"><div>Punkt pomiaru</div><div>Encja w Home Assistant</div><div style="text-align:right">Odczyt</div><div>Korekta (K)</div></div>`;
    for (const [role, meta] of Object.entries(d.roles || {})) {
      html += this._probeRow(role, meta, c, dis);
    }
    html += `<div class="hint">Korekta: różnica względem termometru referencyjnego (np. wszystkie sondy w jednym naczyniu z wodą przed montażem).
      Odczyty −127 °C i nagłe 85,0 °C (reset sondy DS18B20) są odrzucane.</div></div>`;

    html += `<div class="card" style="margin-top:14px"><h3>Bufor i CWU</h3><div class="grid2">
      ${field("buffer_l", "Pojemność bufora (L)", "", c.buffer_l, 'min="0" step="50"')}
      ${field("buffer_max_c", "Bufor pełny przy (°C)", "cały zbiornik w tej temperaturze", c.buffer_max_c, 'min="60" max="95"')}
      ${field("buffer_min_c", "Ciepło użyteczne od (°C)", "poniżej grzejniki już nie grzeją", c.buffer_min_c, 'min="20" max="80"', `domyślnie ${num(stored.buffer_min_c, 0)}`)}
      ${field("buffer_full_c", "Ostrzeżenie „bufor pełny” — góra (°C)", "", c.buffer_full_c, 'min="60" max="95"')}
      ${field("probe_top_h", "Sonda górna — wysokość (%)", "od dna zbiornika", Math.round((c.probe_top_h ?? 0.85) * 100), 'min="50" max="100"')}
      ${field("probe_bottom_h", "Sonda dolna — wysokość (%)", "", Math.round((c.probe_bottom_h ?? 0.15) * 100), 'min="0" max="50"')}
      ${field("dhw_l", "Pojemność zasobnika CWU (L)", "", c.dhw_l, 'min="0" step="10"')}
      ${field("dhw_cold_c", "Woda zimna (°C)", "", c.dhw_cold_c, 'min="0" max="25"')}
      ${field("dhw_target_c", "CWU zadana (°C)", "", c.dhw_target_c, 'min="30" max="80"')}
      ${field("room_target_c", "Pokój — temperatura zadana (°C)", "", c.room_target_c, 'min="10" max="28" step="0.5"')}
    </div></div>`;

    html += `<div class="card" style="margin-top:14px"><h3>Kocioł i drewno</h3><div class="grid2">
      ${field("warn_c", "Ostrzeżenie — kocioł gorący (°C)", "", c.warn_c, 'min="70" max="94"', `profil: ${num(prof.warn_c, 0)}`)}
      ${field("alarm_c", "Alarm przegrzania (°C)", `zawór termiczny: ${num(prof.tzb_c, 0)} °C`, c.alarm_c, 'min="71" max="98"', `profil: ${num(prof.alarm_c, 0)}`)}
      ${field("efficiency", "Sprawność kotła (0–1)", "", c.efficiency, 'min="0.4" max="0.95" step="0.01"', `profil: ${num(prof.efficiency, 2)}`)}
      ${field("wood_kwh_per_kg", "Drewno (kWh/kg)", "sezonowane ~4,0; mokre mniej", c.wood_kwh_per_kg, 'min="2.5" max="5.2" step="0.1"')}
    </div></div>`;

    html += this._controlSettingsHtml(c, dis, field, sel);
    html += `</form><div class="actions">${this._saveMsg ? `<span class="ok">${esc(this._saveMsg)}</span>` : ""}
      ${admin ? `<button class="btn" id="cancel">Anuluj</button><button class="btn primary" id="save">💾 Zapisz</button>` : ""}</div>`;
    html += this._guardHtml();
    return html;
  }

  _entitySelect(name, kind, value, dis, emptyLabel = "— brak —") {
    const filter = KIND_FILTERS[kind] || (() => false);
    const opts = Object.values(this._hass.states || {}).filter(filter)
      .map((st) => [st.entity_id, st.attributes.friendly_name || st.entity_id])
      .sort((a, b) => a[1].localeCompare(b[1], "pl"));
    if (value && !opts.some(([id]) => id === value)) opts.unshift([value, `${value} (nie istnieje)`]);
    return `<select name="${name}" ${dis}><option value="">${esc(emptyLabel)}</option>${opts
      .map(([id, label]) => `<option value="${esc(id)}" ${id === value ? "selected" : ""}>${esc(label)} · ${esc(id)}</option>`).join("")}</select>`;
  }

  _controlSettingsHtml(c, dis, field, sel) {
    const d = this._data;
    const emit = (d.emitter_defaults || {})[c.emitters] || {};
    const weather = Object.fromEntries([["", "— automatycznie (pierwsza encja pogody) —"], ...(d.weather_entities || []).map((w) => [w, w])]);
    const chk = (name, label, value, hint = "") => `<label class="chk"><input type="checkbox" name="${name}" ${value ? "checked" : ""} ${dis}> ${esc(label)}</label>${hint ? `<div class="hint" style="margin-top:2px">${esc(hint)}</div>` : ""}`;
    let html = `<div class="card" style="margin-top:14px"><h3>Sterowanie zaworem i pompą <small>zawór 3-drogowy z siłownikiem 3-punktowym, np. ESBE VRG132 + ARA661</small></h3><div class="grid2">
      <label class="f">Tryb${sel("mode", d.modes || {}, c.mode)}<span class="h">„Wyłączone” = tylko pomiary, nic nie jest przełączane</span></label>
      <label class="f">Siłownik zaworu${sel("valve_type", d.valve_types || {}, c.valve_type)}</label>`;
    if (c.valve_type === "cover") {
      html += `<label class="f">Encja rolety (siłownik)${this._entitySelect("valve_cover", "cover", c.valve_cover, dis)}<span class="h">Shelly 2PM w trybie Cover: impuls odmierza sam Shelly</span></label>`;
    } else if (c.valve_type === "switches") {
      html += `<label class="f">Przekaźnik „otwórz”${this._entitySelect("valve_open_switch", "switch", c.valve_open_switch, dis)}</label>
        <label class="f">Przekaźnik „zamknij”${this._entitySelect("valve_close_switch", "switch", c.valve_close_switch, dis)}<span class="h">ustaw w przekaźnikach auto-wyłączenie jako zabezpieczenie</span></label>`;
    }
    html += `${field("valve_run_s", "Czas przejazdu siłownika (s)", "ARA661: 120 s na 90°", c.valve_run_s, 'min="10" max="600"')}
      <div>${chk("valve_invert", "Odwróć kierunek (otwórz = zimniej)", c.valve_invert, "sprawdź impulsem w trybie ręcznym: „Cieplej” musi podnosić zasilanie")}
      ${c.emitters === "radiators"
        ? chk("safety_dump", "Zrzut ciepła do domu przy przegrzaniu kotła", c.safety_dump, "kocioł gorący → zawór otwarty do grzejników, pompa CO włączona")
        : `<div class="hint">Zrzut ciepła do domu jest wyłączony przy podłogówce (wylewka nie ma własnego zabezpieczenia). Przy przegrzaniu pompa CO i tak zostanie włączona.</div>`}</div>
    </div></div>`;

    const hasDhwPump = !!((c.entities || {}).dhw_pump_switch);
    html += `<div class="card" style="margin-top:14px"><h3>Pompa ładująca CWU <small>sterowanie różnicowe: pompa pracuje tylko wtedy, gdy źródło jest cieplejsze od wody w zasobniku</small></h3>
      ${hasDhwPump ? "" : `<div class="hint" style="margin-top:0">Wybierz przekaźnik pompy w sekcji „Czujniki” (Pompa CWU — przekaźnik). Bez niego te ustawienia nic nie przełączają.</div>`}
      <div class="grid2">
      <label class="f">Skąd grzana jest CWU${sel("dhw_source", d.dhw_sources || {}, c.dhw_source)}<span class="h">z CWU porównywana jest sonda góry bufora albo wyjścia kotła</span></label>
      ${field("dhw_on_diff_k", "Start, gdy źródło cieplejsze o (K)", "", c.dhw_on_diff_k, 'min="2" max="15" step="0.5"')}
      ${field("dhw_off_diff_k", "Stop, gdy różnica spadnie poniżej (K)", "poniżej 0 K pompa staje od razu", c.dhw_off_diff_k, 'min="0.5" max="14" step="0.5"')}
      ${field("dhw_hyst_k", "Ponowne ładowanie po spadku o (K)", `od zadanej CWU (${num(c.dhw_target_c, 0)} °C, sekcja „Bufor i CWU”)`, c.dhw_hyst_k, 'min="1" max="20" step="0.5"')}
      ${field("dhw_max_c", "CWU — górny limit (°C)", "nigdy powyżej, także przy zrzucie ciepła", c.dhw_max_c, 'min="40" max="90"')}
      <div>${chk("dhw_dump", "Zrzut ciepła do CWU przy przegrzaniu kotła (do górnego limitu)", c.dhw_dump,
        "tylko z termostatycznym zaworem mieszającym na wyjściu ciepłej wody — inaczej grozi poparzenie. Bez tej opcji gorący kocioł dogrzewa CWU tylko do zadanej.")}</div>
    </div></div>`;

    html += `<div class="card" style="margin-top:14px"><h3>Krzywa grzewcza <small>zasilanie = pokój + nachylenie × (pokój − zewnątrz) + przesunięcie</small></h3>
      <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr)); gap:14px">
      <div class="grid2">
      ${field("curve_slope", "Nachylenie", "", c.curve_slope, 'min="0.2" max="3" step="0.05"', `domyślnie ${num(emit.curve_slope, 1)}`)}
      ${field("curve_shift", "Przesunięcie (K)", "", c.curve_shift, 'min="-15" max="15" step="0.5"')}
      ${field("supply_min_c", "Zasilanie min. (°C)", "", c.supply_min_c, 'min="15" max="60"')}
      ${field("supply_max_c", "Zasilanie maks. (°C)", "powyżej + 3 K zawór jest zamykany", c.supply_max_c, 'min="30" max="85"', `domyślnie ${num(emit.supply_max_c, 0)}`)}
      ${field("heating_limit_c", "Granica grzania (°C)", "średnia temperatura zewnętrzna", c.heating_limit_c, 'min="5" max="25" step="0.5"')}
      ${field("room_gain", "Korekta pokojowa (K/K)", d.features && d.features.auto ? "0 = bez korekty" : "funkcja PRO", c.room_gain, 'min="0" max="8" step="0.5"')}
      ${field("night_setback_k", "Obniżenie nocne (K)", "", c.night_setback_k, 'min="0" max="6" step="0.5"')}
      ${field("night_from", "Noc od (godz.)", "", c.night_from, 'min="0" max="23"')}
      ${field("night_to", "Noc do (godz.)", "", c.night_to, 'min="0" max="23"')}
      <div>${chk("room_correction", "Korekta według czujnika w pokoju", c.room_correction, d.features && d.features.auto ? "" : "wymaga licencji PRO")}</div>
      </div>
      <div id="curvebox">${this._curveSvg(c)}</div></div></div>`;

    html += `<div class="card" style="margin-top:14px"><h3>Doradca i koszty</h3><div class="grid2">
      <label class="f">Encja pogody (prognoza godzinowa)${sel("weather_entity", weather, c.weather_entity)}</label>
      ${field("light_lead_h", "Rozpalanie z wyprzedzeniem (h)", "zanim kocioł zacznie ładować bufor", c.light_lead_h, 'min="0" max="6" step="0.25"')}
      ${field("burn_hours", "Czas palenia jednego załadunku (h)", "", c.burn_hours, 'min="1" max="12" step="0.5"')}
      ${field("wood_price_kg", "Cena drewna (zł/kg)", "np. 350 zł/mp ≈ 0,8 zł/kg", c.wood_price_kg, 'min="0" max="10" step="0.01"')}
      <div>${chk("advisor_notify", "Powiadomienie „rozpal do…”", c.advisor_notify)}</div>
    </div></div>`;
    return html;
  }

  _curveSvg(c) {
    const W = 360, H = 190, L = 32, R = 8, T = 10, B = 24;
    const room = Number(c.room_target_c) || 21, slope = Number(c.curve_slope) || 1, shift = Number(c.curve_shift) || 0;
    const lo = Number(c.supply_min_c) || 25, hi = Number(c.supply_max_c) || 75;
    const f = (t) => Math.min(Math.max(room + slope * (room - t) + shift, lo), hi);
    const x = (t) => L + (t + 20) / 40 * (W - L - R), y = (v) => T + (90 - v) / 70 * (H - T - B);
    let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Krzywa grzewcza">`;
    for (let v = 20; v <= 90; v += 10) svg += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="rgba(255,255,255,0.06)"/><text x="${L - 4}" y="${y(v) + 3}" text-anchor="end">${v}</text>`;
    for (let t = -20; t <= 20; t += 10) svg += `<text x="${x(t)}" y="${H - 6}" text-anchor="${t === -20 ? "start" : t === 20 ? "end" : "middle"}">${t} °C</text>`;
    let dpath = "";
    for (let t = -20; t <= 20; t += 1) dpath += `${t === -20 ? "M" : "L"}${x(t).toFixed(1)} ${y(f(t)).toFixed(1)} `;
    svg += `<path d="${dpath}" fill="none" stroke="#f7b731" stroke-width="2"/>`;
    for (const t of [-20, -10, 0, 10]) svg += `<circle cx="${x(t)}" cy="${y(f(t))}" r="3" fill="#f7b731"/><text x="${x(t) + 5}" y="${y(f(t)) - 6}" style="fill:#e0e6ed">${f(t).toFixed(0)}°</text>`;
    return svg + `</svg><div class="hint">Zasilanie przy temperaturze zewnętrznej (pokój ${room} °C). Korekta pokojowa i limit z bufora działają na bieżąco.</div>`;
  }

  _guardHtml() {
    const g = this._guard;
    let html = `<div class="card" style="margin-top:14px"><h3>Skrypt bezpieczeństwa na Shelly pompy <small>warstwa 2: działa bez Home Assistanta i bez internetu</small></h3>
      <div class="hint" style="margin-top:0">Skrypt działa na Shelly z przekaźnikiem pompy CO i co 10 s czyta sondy kotła i bufora z tego samego Shelly.
      Gdy kocioł się przegrzewa lub sonda przestaje odpowiadać, włącza pompę CO i (przy grzejnikach) jednorazowo otwiera zawór.
      Pompy nigdy nie wyłącza — to robi Home Assistant. Nie zastępuje zaworu termicznego ani termostatów kotła.</div>`;
    if (!g) {
      html += `<div class="btns"><button class="btn sm" data-act="guard" data-guard="status">Sprawdź skrypt</button></div></div>`;
      return html;
    }
    if (g.loading) return html + `<div class="hint">Łączenie z Shelly…</div></div>`;
    if (g.error && g.supported == null) return html + `<div class="err" style="margin-top:8px">${esc(g.error)}</div>
      <div class="btns"><button class="btn sm" data-act="guard" data-guard="status">Sprawdź ponownie</button></div></div>`;
    const state = !g.supported ? `<span class="pill hot">niedostępny</span>` : g.running ? `<span class="pill on">działa</span>`
      : g.installed ? `<span class="pill hot">zainstalowany, nie działa</span>` : `<span class="pill">nie zainstalowany</span>`;
    html += `<div style="margin-top:10px; font-size:13px">Stan: ${state} ${g.host ? `<span class="muted">· ${esc(g.host)}</span>` : ""}
      ${g.outdated ? ` <span class="pill hot">wymaga aktualizacji (zmienione progi)</span>` : ""}</div>`;
    if (g.reason) html += `<div class="hint">${esc(g.reason)}</div>`;
    for (const n of g.notes || []) html += `<div class="hint">• ${esc(n)}</div>`;
    if (g.error) html += `<div class="err" style="margin-top:6px">${esc(g.error)}</div>`;
    if (this._data.admin) {
      html += `<div class="btns">
        ${g.supported ? `<button class="btn sm primary" data-act="guard" data-guard="install">${g.installed ? "Zaktualizuj skrypt" : "Zainstaluj skrypt"}</button>` : ""}
        ${g.installed ? `<button class="btn sm warn" data-act="guard" data-guard="remove">Usuń</button>` : ""}
        <button class="btn sm" data-act="guard" data-guard="status">Odśwież</button></div>`;
    }
    if (g.code) {
      html += `<details><summary>Kod skryptu (do ręcznego wgrania w panelu Shelly → Scripts)</summary>
        <div class="btns"><button class="btn sm" data-act="copy-guard">Kopiuj</button></div><pre class="code">${esc(g.code)}</pre></details>`;
    }
    return html + `</div>`;
  }

  _probeRow(role, meta, c, dis) {
    const states = Object.values(this._hass.states || {});
    const filter = KIND_FILTERS[meta.kind] || (() => false);
    const current = (c.entities || {})[role] || "";
    const opts = states.filter(filter)
      .map((st) => [st.entity_id, st.attributes.friendly_name || st.entity_id])
      .sort((a, b) => a[1].localeCompare(b[1], "pl"));
    if (current && !opts.some(([id]) => id === current)) opts.unshift([current, `${current} (nie istnieje)`]);
    const options = [`<option value="">— brak —</option>`].concat(
      opts.map(([id, name]) => `<option value="${esc(id)}" ${id === current ? "selected" : ""}>${esc(name)}</option>`));
    const hasOffset = meta.kind === "temperature";
    const off = hasOffset ? ((c.offsets || {})[role] ?? 0) : null;
    return `<div class="probe">
      <div class="name"><b>${esc(meta.label)}</b><span>${esc(ROLE_HINTS[role] || "")}</span></div>
      <select name="ent_${role}" ${dis}>${options.join("")}</select>
      <div class="live" id="live_${role}">${this._liveText(role, current)}</div>
      <div class="off">${hasOffset ? `<input name="off_${role}" type="number" step="0.1" min="-5" max="5" value="${off}" title="Korekta (K)" ${dis}>` : ""}</div>
    </div>`;
  }

  _liveText(role, entityId) {
    if (!entityId) return `<span class="none">—</span>`;
    const st = this._hass.states[entityId];
    if (!st) return `<span class="bad">brak encji</span>`;
    const v = parseFloat(st.state);
    if (!Number.isFinite(v)) return `<span class="bad">${esc(st.state)}</span>`;
    if (v <= -55 || v >= 125) return `<span class="bad">${esc(st.state)} ⚠</span>`;
    return `${esc(st.state)} ${esc(unitOf(st))}`;
  }

  _updateLiveProbes() {
    if (this._view !== "settings") return;
    const form = this.shadowRoot.getElementById("f");
    if (!form) return;
    for (const role of Object.keys((this._data && this._data.roles) || {})) {
      const el = this.shadowRoot.getElementById(`live_${role}`);
      const sel = form.querySelector(`[name="ent_${role}"]`);
      if (el && sel) el.innerHTML = this._liveText(role, sel.value);
    }
  }

  _bindSettings() {
    const root = this.shadowRoot;
    const form = root.getElementById("f");
    if (!form) return;
    form.addEventListener("change", (e) => {
      if (e.target.name && e.target.name.startsWith("ent_")) this._updateLiveProbes();
      this._collect();
      if (["valve_type", "emitters", "ent_dhw_pump_switch"].includes(e.target.name)) this._renderBody();   // other fields / defaults
    });
    form.addEventListener("input", () => {
      this._collect();
      const box = root.getElementById("curvebox");
      if (box) box.innerHTML = this._curveSvg(this._draft);
    });
    const save = root.getElementById("save");
    if (save) save.addEventListener("click", () => this._save());
    const cancel = root.getElementById("cancel");
    if (cancel) cancel.addEventListener("click", () => { this._draft = null; this._saveMsg = ""; this._renderBody(); });
  }

  _collect() {
    const form = this.shadowRoot.getElementById("f");
    if (!form || !this._draft) return;
    const c = this._draft;
    const nums = ["buffer_l", "buffer_max_c", "buffer_min_c", "buffer_full_c", "dhw_l", "dhw_cold_c", "dhw_target_c",
      "room_target_c", "warn_c", "alarm_c", "efficiency", "wood_kwh_per_kg", "valve_run_s", "curve_slope", "curve_shift",
      "supply_min_c", "supply_max_c", "heating_limit_c", "room_gain", "night_setback_k", "night_from", "night_to",
      "light_lead_h", "burn_hours", "wood_price_kg", "dhw_on_diff_k", "dhw_off_diff_k", "dhw_hyst_k", "dhw_max_c"];
    const checks = ["enabled", "notify", "valve_invert", "safety_dump", "room_correction", "advisor_notify", "dhw_dump"];
    const selects = ["profile", "emitters", "mode", "valve_type", "valve_cover", "valve_open_switch", "valve_close_switch", "weather_entity", "dhw_source"];
    for (const el of form.elements) {
      if (!el.name) continue;
      if (checks.includes(el.name)) c[el.name] = el.checked;
      else if (selects.includes(el.name)) c[el.name] = el.value;
      else if (el.name.startsWith("ent_")) { c.entities = c.entities || {}; c.entities[el.name.slice(4)] = el.value; }
      else if (el.name.startsWith("off_")) { c.offsets = c.offsets || {}; c.offsets[el.name.slice(4)] = el.value === "" ? 0 : parseFloat(el.value); }
      else if (el.name === "probe_top_h" || el.name === "probe_bottom_h") c[el.name] = el.value === "" ? null : parseFloat(el.value) / 100;
      else if (nums.includes(el.name)) c[el.name] = el.value === "" ? null : parseFloat(el.value);
    }
  }

  async _save() {
    this._collect();
    const cfg = { ...this._draft };
    // fields left at the profile / emitter default are stored empty, so a later change of the
    // boiler profile or the emitters brings its own defaults
    const d = this._data.config;
    const prof = (this._data.profile_defaults || {})[d.profile] || {};
    const emit = (this._data.emitter_defaults || {})[d.emitters] || {};
    for (const [k, def] of [["warn_c", prof.warn_c], ["alarm_c", prof.alarm_c], ["efficiency", prof.efficiency],
      ["buffer_min_c", emit.buffer_min_c], ["curve_slope", emit.curve_slope], ["supply_max_c", emit.supply_max_c]]) {
      if (cfg[k] != null && def != null && Math.abs(cfg[k] - def) < 1e-6) cfg[k] = null;
    }
    if (cfg.safety_dump === emit.safety_dump) cfg.safety_dump = null;
    for (const k of ["tzb_c", "min_return_c"]) delete cfg[k];
    try {
      this._data = await this._hass.callWS({ type: "smartinghome/heating/save", config: cfg });
      this._draft = null;
      this._saveMsg = cfg.enabled ? "✓ Zapisano — odczyty liczone od teraz" : "✓ Zapisano — moduł wyłączony (panel zniknie z menu)";
      this._chartTs = 0;
    } catch (e) {
      this._saveMsg = "";
      this._error = "";
      alert(`Nie zapisano: ${e.message || e.code || e}`);
      return;
    }
    this._renderBody();
  }
}

if (!customElements.get("smartinghome-heating-panel")) {
  customElements.define("smartinghome-heating-panel", SmartingHomeHeatingPanel);
}
