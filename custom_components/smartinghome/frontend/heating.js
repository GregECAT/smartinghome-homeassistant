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
  floor0_temp: "np. Shelly H&T w środku tej kondygnacji, z dala od grzejnika i okna",
  floor1_temp: "np. Shelly H&T w środku tej kondygnacji, z dala od grzejnika i okna",
  floor2_temp: "np. Shelly H&T w środku tej kondygnacji, z dala od grzejnika i okna",
  floor0_humidity: "opcjonalnie", floor1_humidity: "opcjonalnie", floor2_humidity: "opcjonalnie",
  dhw_pump_switch: "opcjonalnie — przekaźnik pompy ładującej CWU (np. Shelly 1PM Mini)",
  dhw_pump_power: "opcjonalnie — moc pompy CWU (wykrywa awarię pompy)",
};

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v, d = 1) => (v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toLocaleString("pl-PL", { minimumFractionDigits: d, maximumFractionDigits: d }));
const fmtTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString("pl-PL", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const hhmm = (ts) => (ts ? new Date(ts * 1000).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" }) : "—");
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
    this._onFs = () => this._fsChanged();
    this._onResize = () => this._measure();
    this._onVis = () => { if (!document.hidden && this._isFs()) this._wake(true); };
    document.addEventListener("fullscreenchange", this._onFs);
    document.addEventListener("webkitfullscreenchange", this._onFs);
    document.addEventListener("visibilitychange", this._onVis);
    window.addEventListener("resize", this._onResize);
    requestAnimationFrame(() => this._measure());
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    document.removeEventListener("fullscreenchange", this._onFs);
    document.removeEventListener("webkitfullscreenchange", this._onFs);
    document.removeEventListener("visibilitychange", this._onVis);
    window.removeEventListener("resize", this._onResize);
    if (this._cssFs) this._setCssFs(false);   // leaving the panel leaves its full screen too
    this._wake(false);
  }

  // ── full screen (wall tablet / TV): the panel alone, the screen kept awake ──
  // Inside Home Assistant the panel sits in nested shadow roots: document.fullscreenElement is the
  // outermost shadow host there, never this element. The panel's own root node reports it right;
  // and whatever is full screen while this panel is on screen, the button has to leave it.
  // The Home Assistant app on iPad / iPhone (and some kiosk browsers) has no Fullscreen API: there
  // the panel covers the whole window itself (sidebar included) — like the Smarting HOME panel does.
  _isFs() {
    if (this._cssFs) return true;
    const root = this.getRootNode && this.getRootNode();
    const own = root && (root.fullscreenElement || root.webkitFullscreenElement);
    return own === this || !!(document.fullscreenElement || document.webkitFullscreenElement);
  }

  _toggleFullscreen() {
    if (this._cssFs) { this._setCssFs(false); return; }
    if (document.fullscreenElement || document.webkitFullscreenElement) {
      const exit = document.exitFullscreen || document.webkitExitFullscreen;
      if (exit) { const p = exit.call(document); if (p && p.catch) p.catch(() => {}); }
      return;
    }
    const req = this.requestFullscreen || this.webkitRequestFullscreen;
    if (!req) { this._setCssFs(true); return; }
    try {
      const p = req.call(this, { navigationUI: "hide" });
      if (p && p.catch) p.catch(() => this._setCssFs(true));   // refused (no gesture, iframe policy…)
    } catch (e) {
      this._setCssFs(true);
    }
  }

  _setCssFs(on) {
    this._cssFs = !!on;
    this.toggleAttribute("data-fs", this._cssFs);
    if (this._cssFs && !this._onKey) {
      this._onKey = (e) => { if (e.key === "Escape") this._setCssFs(false); };
      document.addEventListener("keydown", this._onKey);
    } else if (!this._cssFs && this._onKey) {
      document.removeEventListener("keydown", this._onKey);
      this._onKey = null;
    }
    this._fsChanged();
  }

  _fsChanged() {
    const on = this._isFs();
    const b = this.shadowRoot.getElementById("fs");
    if (b) { b.textContent = on ? "✕" : "⛶"; b.title = on ? "Zamknij pełny ekran" : "Pełny ekran — kotłownia na cały ekran, ekran nie gaśnie"; }
    this._wake(on);
    this._measure();
  }

  async _wake(on) {
    try {
      if (on && "wakeLock" in navigator) {
        if (!this._lock || this._lock.released) this._lock = await navigator.wakeLock.request("screen");
      } else if (this._lock) {
        await this._lock.release();
        this._lock = null;
      }
    } catch (e) {
      this._lock = null;      // not allowed here (no HTTPS, battery saver) — full screen still works
    }
  }

  // the hero fills the screen below the header. Home Assistant sets hass before the panel is
  // shown, so the first measurement can be 0 — keep the CSS fallback and measure again once laid out.
  _measure() {
    const root = this.shadowRoot;
    const h = root && root.querySelector(".header");
    const w = root && root.querySelector(".wrap");
    if (!h || !w) return;
    const px = h.offsetHeight;
    if (px > 0) {
      if (this._hdr !== px) {
        this._hdr = px;
        w.style.setProperty("--hdr", `${px}px`);
      }
    } else if (this.isConnected && !this._measureRetry) {
      this._measureRetry = requestAnimationFrame(() => { this._measureRetry = 0; this._measure(); });
    }
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
    const ids = this._chartSeries().map(([role]) => ents[role]).filter(Boolean);
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
        form .grid2.floorsg { grid-template-columns:repeat(2,minmax(0,1fr)); }   /* one storey per row: name | area */
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
        /* hero: the boiler room on the first screen */
        :host(:fullscreen) { overflow-y:auto; background:#0a1628; }
        :host([data-fs]) { position:fixed; inset:0; z-index:99999; height:auto; overflow-y:auto; -webkit-overflow-scrolling:touch;
          background:#0a1628; padding:env(safe-area-inset-top,0px) env(safe-area-inset-right,0px) env(safe-area-inset-bottom,0px) env(safe-area-inset-left,0px);
          --sa:calc(env(safe-area-inset-top,0px) + env(safe-area-inset-bottom,0px)); }
        :host([data-fs]) .menu-btn { display:none !important; }
        .hero { display:flex; flex-direction:column; gap:10px; min-height:560px; margin-bottom:14px;
          height:calc(100vh - var(--hdr, 58px) - 26px - var(--sa, 0px)); height:calc(100dvh - var(--hdr, 58px) - 26px - var(--sa, 0px)); }
        .hero-top { flex:0 0 auto; display:flex; flex-direction:column; gap:6px; }
        .hero-main { flex:1 1 auto; min-height:0; padding:6px 8px; display:flex; }
        .hero-main svg { width:100%; height:100%; display:block; }
        .hero-chart { flex:0 0 25%; min-height:150px; display:flex; flex-direction:column; padding:10px 14px 8px; }
        .hero-chart h3 { margin-bottom:2px; }
        .hc { flex:1 1 auto; min-height:0; display:flex; flex-direction:column; }
        .hc svg { flex:1 1 auto; min-height:0; width:100%; }
        .hc .legend { margin-top:2px; }
        .chips { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
        .chip { display:inline-flex; align-items:center; gap:6px; padding:6px 12px; border-radius:999px; font-size:13px; color:#cbd5e1;
          background:rgba(20,30,48,0.6); border:1px solid rgba(255,255,255,0.08); white-space:nowrap; }
        .chip b { color:#fff; font-weight:600; }
        .chip.ghost { background:transparent; border-color:transparent; color:#64748b; margin-left:auto; }
        .chip.bad { background:rgba(231,76,60,0.15); border-color:rgba(231,76,60,0.5); color:#fca5a5; }
        .chip.warn { background:rgba(247,183,49,0.1); border-color:rgba(247,183,49,0.4); color:#fcd34d; }
        .chip.hot { border-color:rgba(247,183,49,0.45); }
        .led { width:8px; height:8px; border-radius:50%; background:#64748b; display:inline-block; }
        .led.on { background:#2ecc71; box-shadow:0 0 8px #2ecc71; }
        .alarm { border-radius:10px; padding:7px 12px; font-size:13px; line-height:1.4; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
          background:rgba(0,212,255,0.07); border:1px solid rgba(0,212,255,0.25); }
        .alarm.warn { background:rgba(247,183,49,0.08); border-color:rgba(247,183,49,0.35); }
        .alarm.err { background:rgba(231,76,60,0.14); border-color:rgba(231,76,60,0.55); animation:alarmp 1.6s ease-in-out infinite alternate; }
        .alarm b { color:#fff; } .alarm span { color:#cbd5e1; } .alarm .act { color:#94a3b8; }
        .t-h { fill:#e0e6ed; font-size:17px; font-weight:700; }
        .t-h2 { fill:#e0e6ed; font-size:14px; font-weight:700; }
        .t-big { font-size:36px; font-weight:800; }
        .t-big2 { fill:#e0e6ed; font-size:28px; font-weight:800; }
        .t-xl { font-size:46px; font-weight:800; }
        .t-l { font-size:20px; font-weight:700; }
        .t-m { font-size:14px; font-weight:600; }
        .t-s { fill:#94a3b8; font-size:12px; }
        .t-s2 { font-size:12.5px; font-weight:600; }
        svg text.t-in { fill:#0a1628; }
        svg text.dim { fill:#64748b; }
        .pc { fill:none; stroke:#060c18; stroke-width:13; stroke-linecap:round; stroke-linejoin:round; }
        .pp { fill:none; stroke-width:7; stroke-linecap:round; stroke-linejoin:round; }
        .pf { fill:none; stroke:rgba(255,255,255,0.85); stroke-width:2.5; stroke-linecap:round; stroke-dasharray:3 15;
          animation:flow 1s linear infinite; animation-delay:var(--ph-flow,0ms); }
        .pump { fill:#0f172a; stroke:#64748b; stroke-width:2.5; }
        .pump.on { stroke:#2ecc71; filter:drop-shadow(0 0 6px rgba(46,204,113,0.6)); }
        .imp { stroke:#64748b; stroke-width:2.5; stroke-linecap:round; } .imp.on { stroke:#2ecc71; }
        .spin { transform-box:fill-box; transform-origin:center; animation:spin 1.2s linear infinite; animation-delay:var(--ph-spin,0ms); }
        .flame { transform-box:fill-box; transform-origin:50% 100%; animation:flick 0.7s ease-in-out infinite alternate; animation-delay:var(--ph-flick,0ms); }
        .flame.f2 { animation-duration:0.55s; } .flame.f3 { animation-duration:0.8s; } .flame.low { opacity:0.55; }
        .hotpulse { animation:hp 1.2s ease-in-out infinite alternate; animation-delay:var(--ph-pulse,0ms); }
        .bub { fill:rgba(255,255,255,0.65); animation:bub 3s linear infinite; }
        .draw { stroke-dasharray:1; stroke-dashoffset:1; animation:draw 1.6s ease-out forwards; }
        .endp { opacity:0; animation:fadein 0.5s ease-out forwards; }
        @keyframes flow { to { stroke-dashoffset:-18; } }
        @keyframes spin { to { transform:rotate(360deg); } }
        @keyframes flick { from { transform:scaleY(0.82) skewX(-4deg); opacity:0.8; } to { transform:scaleY(1.06) skewX(4deg); opacity:1; } }
        @keyframes hp { from { stroke-opacity:0.35; } to { stroke-opacity:1; } }
        @keyframes bub { 0% { transform:translateY(0); opacity:0; } 15% { opacity:0.7; } 100% { transform:translateY(-330px); opacity:0; } }
        @keyframes draw { to { stroke-dashoffset:0; } }
        @keyframes fadein { to { opacity:1; } }
        @keyframes alarmp { from { box-shadow:0 0 0 rgba(231,76,60,0); } to { box-shadow:0 0 18px rgba(231,76,60,0.35); } }
        @media (prefers-reduced-motion: reduce) {
          .pf, .spin, .flame, .bub, .hotpulse, .alarm.err { animation:none; }
          .draw { stroke-dasharray:none; animation:none; } .endp { opacity:1; animation:none; }
        }
        @media (max-width:700px), (max-height:520px) {
          .hero { height:auto; min-height:0; }
          .hero-main svg { height:auto; }
          .hero-chart { flex:none; height:auto; }
          .hc svg { flex:none; height:auto; }
          .chip.ghost { margin-left:0; }
          .alarm { white-space:normal; }
        }
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
            <button class="btn" id="fs" title="Pełny ekran — kotłownia na cały ekran, ekran nie gaśnie">⛶</button>
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
      this._skel = false;
      root.querySelectorAll("#views button").forEach((x) => x.classList.toggle("on", x === b));
      if (this._view === "overview") this._chartTs = 0;
      this._load();
    }));
    root.querySelector("#refresh").addEventListener("click", () => { this._chartTs = 0; this._load(); });
    root.querySelector("#fs").addEventListener("click", () => this._toggleFullscreen());
    this._measure();
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
      this._skel = false;
      body.innerHTML = `<div class="banner err">Nie udało się pobrać danych modułu: <b>${esc(this._error)}</b></div>`;
      return;
    }
    if (!this._data) return;
    if (this._view === "settings") {
      this._skel = false;
      body.innerHTML = this._settingsHtml();
      this._bindSettings();
      return;
    }
    this._renderOverview();
  }

  // ── overview ──────────────────────────────────────────────────────────────
  // The first screen is the boiler room itself (hero: status strip, live diagram, 24 h chart);
  // the cards follow below. Each part is replaced only when its HTML changed, so the
  // animations keep running and the chart's draw-in plays only when new history arrives.
  _renderOverview() {
    const body = this.shadowRoot.getElementById("body");
    const d = this._data;
    if (!d.enabled) {
      this._skel = false;
      body.innerHTML = `<div class="banner warn">Moduł ogrzewania jest wyłączony. Włącz go w zakładce <b>Ustawienia</b>.</div>`;
      return;
    }
    if (!this._skel) {
      body.innerHTML = `<section class="hero" id="hero">
          <div class="hero-top" id="heroTop"></div>
          <div class="hero-main card" id="heroMain"></div>
          <div class="hero-chart card"><h3>Ostatnie ${CHART_HOURS} h <small>°C</small></h3><div class="hc" id="heroChart"></div></div>
        </section>
        <div id="rest"></div>`;
      this._skel = true;
      this._parts = {};
    }
    if (!this._hdr) this._measure();
    const st = d.status || {};
    this._patch("heroTop", this._heroTopHtml(st));
    if (this._patch("heroMain", this._heroSvg(st))) this._phase(this.shadowRoot.getElementById("heroMain"));
    const narrow = this.clientWidth > 0 && this.clientWidth < 700;
    this._patch("heroChart", narrow ? this._chartSvg(600, 300, true) : this._chartSvg(1200, 200, true));
    const focused = this.shadowRoot.activeElement;
    if (!(focused && focused.tagName === "INPUT")) this._patch("rest", this._restHtml(st));   // typing the wood weight
  }

  _patch(id, html) {
    if (this._parts[id] === html) return false;
    const el = this.shadowRoot.getElementById(id);
    if (!el) return false;
    el.innerHTML = html;
    this._parts[id] = html;
    return true;
  }

  // a re-rendered diagram continues its animations where they were (negative delay = the current phase)
  _phase(el) {
    const now = Date.now();
    for (const [k, ms] of [["flow", 1000], ["spin", 1200], ["flick", 1400], ["pulse", 2400], ["bub", 3000]]) {
      el.style.setProperty(`--ph-${k}`, `${-(now % ms)}ms`);
    }
  }

  _heroTopHtml(st) {
    const d = this._data, c = d.config, ctl = st.control || {}, b = st.boiler || {}, a = st.advice || {};
    const modeLbl = { off: "Wyłączone", manual: "Ręczne", auto: "Automatyczne", summer: "Lato" }[c.mode] || c.mode;
    const chips = [
      `<span class="chip"><i class="led ${c.mode === "off" ? "" : "on"}"></i>Tryb <b>${esc(modeLbl)}</b></span>`,
      `<span class="chip">${b.firing ? "🔥" : "◻"} Kocioł <b style="color:${STATE_COLOR[b.state] || "#e0e6ed"}">${esc(b.label || "—")}</b></span>`,
    ];
    if (ctl.dump) chips.push(`<span class="chip bad">⚠ Zrzut ciepła do domu</span>`);
    if (ctl.limiter) chips.push(`<span class="chip warn">Ogranicznik zasilania</span>`);
    if (st.buffer) chips.push(`<span class="chip">Bufor <b>${num(st.buffer.charge_pct, 0)}%</b> · przyjmie <b>${num(st.buffer.wood_max_kg, 0)} kg</b></span>`);
    if (!a.locked && a.empty_at) chips.push(`<span class="chip">⏳ Ciepła do <b>${hhmm(a.empty_at)}</b></span>`);
    if (!a.locked && !a.firing && a.light_at) chips.push(`<span class="chip hot">🪵 Rozpal do <b>${hhmm(a.light_at)}</b>${a.wood_kg != null ? ` · ${num(a.wood_kg, 0)} kg` : ""}</span>`);
    if (st.runtime_h != null && (a.locked || !a.empty_at)) chips.push(`<span class="chip">⏳ Bufor na <b>${fmtHours(st.runtime_h)}</b></span>`);
    chips.push(`<span class="chip ghost">odczyt ${hhmm(st.updated)}</span>`);
    let html = `<div class="chips">${chips.join("")}</div>`;
    const ents = c.entities || {};
    if (!["boiler_out", "buffer_top", "buffer_bottom"].some((r) => ents[r])) {
      html += `<div class="alarm warn">Nie przypisano jeszcze czujników — w zakładce <b>Ustawienia</b> wybierz sondy kotła, bufora i CWU.</div>`;
    }
    for (const al of st.alarms || []) {
      html += `<div class="alarm ${LEVEL_CLASS[al.level] || "info"}" title="${esc(al.action || "")}">${LEVEL_ICON[al.level] || ""} <b>${esc(al.title)}</b>
        <span>${esc(al.message)}</span>${al.action ? ` <span class="act">→ ${esc(al.action)}</span>` : ""}</div>`;
    }
    return html;
  }

  _restHtml(st) {
    let html = `<div class="grid kpis">${this._kpis(st)}</div>`;
    if (this._msg) html += `<div class="banner ${this._msgErr ? "err" : ""}" style="margin-top:14px">${esc(this._msg)}</div>`;
    html += `<div class="grid cols">
      <div class="card">${this._controlHtml(st)}</div>
      <div class="card">${this._advisorHtml(st)}</div>
    </div>`;
    html += `<div class="grid cols">
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
    const cfg = this._data.config;
    if ((cfg.floors || 1) >= 2) {
      const rows = (st.floors || []).map((f) => `${f.ref ? "◉ " : ""}${esc(f.name)}: <b style="color:#e0e6ed">${f.temp_c != null ? `${num(f.temp_c)} °C` : "—"}</b>${f.humidity != null ? ` · ${num(f.humidity, 0)}%` : ""}`);
      out.push(`<div class="card kpi"><div class="lbl">Dom — średnio</div>
        <div class="val">${num(st.house_c)}<small>°C</small></div>
        <div class="note">${rows.join("<br>")}<br>zadana ${num(cfg.room_target_c)} °C${cfg.floor_areas && cfg.floor_areas.some((a) => a) ? " · średnia ważona powierzchnią" : ""}</div></div>`);
    } else if (room.value != null || st.humidity != null) {
      out.push(`<div class="card kpi"><div class="lbl">Pokój</div>
        <div class="val">${num(room.value)}<small>°C</small></div>
        <div class="note">zadana ${num(this._data.config.room_target_c)} °C${st.humidity != null ? ` · wilgotność ${num(st.humidity, 0)}%` : ""}</div></div>`);
    }
    return out.join("");
  }

  // ── hero: the whole boiler room on one screen ─────────────────────────────
  // viewBox 1200 × 520. Pipes are drawn in the direction of flow: the dashes of a running
  // circuit move that way. Re-rendering keeps the animations in phase (_phase).
  _heroSvg(st) {
    const d = this._data, c = d.config, p = st.probes || {}, b = st.boiler || {}, ctl = st.control || {};
    const v = (r) => (p[r] && p[r].value != null ? p[r].value : null);
    const lbl = (r) => { const x = v(r); return x == null ? (p[r] && p[r].status !== "missing" ? "⚠" : "—") : `${num(x)}°`; };
    const tb = v("boiler_out"), top = v("buffer_top"), bot = v("buffer_bottom"), dhwT = v("dhw");
    const sup = v("heating_supply"), ret = v("heating_return");
    const fire = !!b.firing, over = b.state === "overheat";
    const sc = STATE_COLOR[b.state] || "#64748b";
    const pump = ctl.pump || {}, dp = ctl.dhw_pump || {}, valve = ctl.valve || {};
    const coOn = pump.on === true, dhwOn = dp.on === true;
    const fromBoiler = c.dhw_source === "boiler_out";
    const buf = st.buffer, dhw = st.dhw;
    const pw = (x) => (x != null && x >= 1 ? ` · ${num(x, 0)} W` : "");
    const pipe = (dd, color, flow) => `<path class="pc" d="${dd}"/><path class="pp" d="${dd}" stroke="${color}"/>${flow ? `<path class="pf" d="${dd}"/>` : ""}`;
    const pumpSym = (x, y, on, r = 16) => `<circle cx="${x}" cy="${y}" r="${r}" class="pump${on ? " on" : ""}"/>
      <g class="${on ? "spin" : ""}"><path class="imp${on ? " on" : ""}" d="M${x} ${y - r + 5}V${y + r - 5}M${x - r + 5} ${y}H${x + r - 5}M${x - r + 8} ${y - r + 8}L${x + r - 8} ${y + r - 8}M${x + r - 8} ${y - r + 8}L${x - r + 8} ${y + r - 8}"/></g>
      <circle cx="${x}" cy="${y}" r="3.5" fill="#cbd5e1"/>`;
    // buffer probe heights → gradient stops and labels
    const hTop = Number(c.probe_top_h) || 0.85, hBot = Number(c.probe_bottom_h) || 0.15;
    const yTop = 470 - 400 * hTop, yBot = 470 - 400 * hBot;
    const supC = tempColor(sup ?? top), retC = tempColor(ret ?? (sup != null ? sup - 10 : bot));
    const feedC = tempColor(fromBoiler ? tb : top);
    const pos = Math.round(valve.position ?? 0);
    const feed = fromBoiler ? "M190 160H210V30H540V100a10 10 0 0 1 0 20V390H640" : "M495 175H540V390H640";
    const coilBack = fromBoiler ? "M640 464H560V512H215V390H190" : "M640 464H560V445H495";
    const coil = "M640 392H728q14 0 14 12t-14 12H662q-14 0-14 12t14 12H728q14 0 14 12t-14 12H640";

    let s = `<svg viewBox="0 0 1200 520" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Kotłownia na żywo">
      <defs>
        <linearGradient id="hgBuf" x1="0" y1="1" x2="0" y2="0">
          <stop offset="0" stop-color="${tempColor(bot ?? top)}"/><stop offset="${hBot}" stop-color="${tempColor(bot ?? top)}"/>
          <stop offset="${hTop}" stop-color="${tempColor(top ?? bot)}"/><stop offset="1" stop-color="${tempColor(top ?? bot)}"/>
        </linearGradient>
        <linearGradient id="hgShine" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stop-color="#fff" stop-opacity=".22"/><stop offset=".35" stop-color="#fff" stop-opacity="0"/>
          <stop offset=".85" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".25"/>
        </linearGradient>
        <linearGradient id="hgSteel" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stop-color="#2a3a52"/><stop offset=".5" stop-color="#1c283b"/><stop offset="1" stop-color="#111a29"/>
        </linearGradient>
        <radialGradient id="hgFire" cx=".5" cy="1" r=".9">
          <stop offset="0" stop-color="#ffd166" stop-opacity=".9"/><stop offset=".5" stop-color="#e67e22" stop-opacity=".45"/>
          <stop offset="1" stop-color="#e74c3c" stop-opacity="0"/>
        </radialGradient>
        <filter id="hgGlow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="6"/></filter>
      </defs>
      <line x1="10" y1="514" x2="1190" y2="514" stroke="rgba(255,255,255,.06)"/>`;

    // pipes first (under the equipment)
    s += pipe("M190 180H240", tempColor(tb), fire);
    s += pipe("M300 180H318V100H345", tempColor(tb), fire);
    s += pipe("M345 440H318V210H300", tempColor(bot), fire);
    s += pipe("M240 210H215V390H190", tempColor(bot), fire);
    s += pipe("M495 110H559", tempColor(top), coOn);
    s += pipe("M591 110H647", supC, coOn);
    s += pipe("M683 110H760V170H780", supC, coOn);
    s += pipe("M780 315H600V360H550a10 10 0 0 0 -20 0H495", retC, coOn);
    s += pipe(feed, feedC, dhwOn);
    s += pipe(coilBack, tempColor(dhwT), dhwOn);

    // boiler
    s += `<text x="110" y="136" text-anchor="middle" class="t-h">Kocioł</text>
      ${over || fire ? `<rect x="22" y="142" width="176" height="276" rx="18" fill="${over ? "#e74c3c" : "#e67e22"}" opacity="${over ? 0.35 : 0.18}" filter="url(#hgGlow)"/>` : ""}
      <rect x="30" y="150" width="160" height="260" rx="14" fill="url(#hgSteel)" stroke="${sc}" stroke-width="3" class="${over ? "hotpulse" : ""}"/>
      <text x="110" y="205" text-anchor="middle" class="t-big" style="fill:${sc}">${lbl("boiler_out")}</text>
      <text x="110" y="232" text-anchor="middle" class="t-m" style="fill:${sc}">${esc(b.label || "brak danych")}</text>
      ${b.trend_k_min != null ? `<text x="110" y="252" text-anchor="middle" class="t-s">${b.trend_k_min > 0.2 ? "↑" : b.trend_k_min < -0.2 ? "↓" : "→"} ${num(b.trend_k_min, 2)} K/min</text>` : ""}
      <rect x="55" y="270" width="110" height="115" rx="10" fill="#0b1220" stroke="#475569"/>`;
    if (fire) {
      const low = b.state === "burnout" ? " low" : "";
      s += `<rect x="58" y="273" width="104" height="109" rx="8" fill="url(#hgFire)"/>
        <g class="flame${low}"><path d="M78 380C66 350 92 334 86 300C108 326 114 348 104 380Z" fill="#e67e22"/></g>
        <g class="flame f2${low}"><path d="M98 380C90 342 120 326 111 288C136 320 141 352 128 380Z" fill="#f39c12"/></g>
        <g class="flame f3${low}"><path d="M122 380C115 352 141 341 136 312C154 336 154 360 144 380Z" fill="#f7b731"/></g>
        <path d="M66 380H156" stroke="#ffd166" stroke-width="3" stroke-linecap="round" opacity=".7"/>`;
    } else {
      s += `<path d="M70 372H150" stroke="#334155" stroke-width="3" stroke-linecap="round"/>`;
    }

    // Laddomat (its pump runs with the boiler, controlled by the boiler)
    s += `<rect x="240" y="160" width="60" height="70" rx="9" fill="#0f172a" stroke="#475569"/>
      ${pumpSym(270, 195, fire, 14)}
      <text x="270" y="248" text-anchor="middle" class="t-s">Laddomat</text>`;

    // buffer
    s += `<text x="420" y="52" text-anchor="middle" class="t-h">Bufor ${num(c.buffer_l, 0)} L</text>
      <rect x="345" y="70" width="150" height="400" rx="34" fill="url(#hgBuf)" stroke="#64748b" stroke-width="2"/>
      <rect x="345" y="70" width="150" height="400" rx="34" fill="url(#hgShine)"/>`;
    if (fire) {
      s += [[372, 0], [398, -0.8], [426, -1.7], [452, -2.4], [470, -1.2]].map(([x, dl]) =>
        `<circle cx="${x}" cy="452" r="3.5" class="bub" style="animation-delay:calc(var(--ph-bub,0ms) + ${dl}s)"/>`).join("");
    }
    s += `<path d="M345 ${yTop}h14M481 ${yTop}h14M345 ${yBot}h14M481 ${yBot}h14" stroke="rgba(10,22,40,.55)" stroke-width="2"/>
      <text x="420" y="${yTop + 8}" text-anchor="middle" class="t-in t-l">${lbl("buffer_top")}</text>
      <text x="420" y="${yBot + 8}" text-anchor="middle" class="t-in t-l">${lbl("buffer_bottom")}</text>`;
    if (buf) {
      s += `<text x="420" y="266" text-anchor="middle" class="t-in t-xl">${num(buf.charge_pct, 0)}%</text>
        <text x="420" y="292" text-anchor="middle" class="t-in t-m">${num(buf.energy_kwh)} / ${num(buf.capacity_kwh, 0)} kWh</text>
        <text x="420" y="312" text-anchor="middle" class="t-in t-s2">przyjmie ${num(buf.wood_max_kg, 0)} kg</text>`;
    }

    // mixing valve (3-way, motorised)
    s += `<path d="M575 110L559 100V120ZM575 110L591 100V120ZM575 110L565 126H585Z" fill="#0f172a" stroke="#cbd5e1" stroke-width="1.5" stroke-linejoin="round"/>
      <path d="M575 94V110" stroke="#94a3b8" stroke-width="2"/>
      <rect x="564" y="76" width="22" height="18" rx="3" fill="#1e293b" stroke="${valve.busy ? "#f7b731" : "#64748b"}" class="${valve.busy ? "hotpulse" : ""}"/>
      <text x="575" y="89" text-anchor="middle" class="t-s">M</text>
      <text x="575" y="146" text-anchor="middle" class="t-s">${valve.configured ? `${valve.known ? "" : "~"}${pos}%` : "zawór"}</text>`;

    // CH pump and supply
    s += pumpSym(665, 110, coOn) + `<text x="665" y="148" text-anchor="middle" class="t-s">pompa CO${coOn ? pw((st.pump || {}).power_w) : ""}</text>
      ${!pump.configured ? `<text x="665" y="163" text-anchor="middle" class="t-s dim">nieprzypisana</text>` : ""}
      <text x="722" y="98" text-anchor="middle" class="t-l" style="fill:${supC}">${lbl("heating_supply")}</text>
      ${ctl.supply_target != null ? `<text x="722" y="136" text-anchor="middle" class="t-s">zad. ${num(ctl.supply_target, 0)}°</text>` : ""}`;

    // house — one room, or storey by storey (the top one under the roof when there are three)
    const room = v("room");
    const radC = coOn ? supC : "#334155";
    const heatTxt = ctl.dump ? "zrzut ciepła" : coOn ? "grzanie" : ctl.heating_needed ? "zapotrzebowanie" : "bez grzania";
    const target = num(ctl.room_target ?? c.room_target_c);
    s += `<path d="M780 160L895 62L1010 160V340H780Z" fill="rgba(255,255,255,.035)" stroke="#475569" stroke-width="2" stroke-linejoin="round"/>`;
    if ((c.floors || 1) >= 2) {
      const fl = st.floors || [];
      const bands = c.floors >= 3 ? [[0, 250, 340], [1, 160, 250], [2, 62, 160]] : [[0, 250, 340], [1, 160, 250]];
      s += `<path d="M782 250H1008" stroke="#334155" stroke-width="1.5" stroke-dasharray="5 5"/>`;
      if (c.floors < 3) s += `<text x="895" y="132" text-anchor="middle" class="t-s">Dom</text>`;
      for (const [i, y0, y1] of bands) {
        const f = fl[i] || {};
        const name = `${f.ref ? "◉ " : ""}${esc(f.name || "")}`;
        const tt = f.temp_c != null ? `${num(f.temp_c)}°` : "—";
        if (y0 === 62) {      // under the roof
          s += `<text x="895" y="106" text-anchor="middle" class="t-s">${name}${f.humidity != null ? ` · ${num(f.humidity, 0)}%` : ""}</text>
            <text x="895" y="140" text-anchor="middle" class="t-l" style="fill:${f.temp_c != null ? "#e0e6ed" : "#64748b"}">${tt}</text>`;
          continue;
        }
        const cy = (y0 + y1) / 2;
        s += `<text x="792" y="${y0 + 20}" class="t-s">${name}</text>
          <text x="868" y="${cy + 18}" text-anchor="middle" class="t-big2" style="fill:${f.temp_c != null ? "#e0e6ed" : "#64748b"}">${tt}</text>
          <text x="868" y="${cy + 36}" text-anchor="middle" class="t-s ${f.temp_c != null ? "" : "dim"}">${f.temp_c == null ? "brak czujnika" : f.humidity != null ? `${num(f.humidity, 0)}%` : ""}</text>
          ${coOn ? `<rect x="944" y="${cy - 14}" width="54" height="34" rx="6" fill="${radC}" opacity=".3" filter="url(#hgGlow)"/>` : ""}
          <rect x="948" y="${cy - 10}" width="46" height="26" rx="4" fill="#0f172a" stroke="${radC}" stroke-width="1.5"/>
          ${[0, 1, 2, 3].map((k) => `<rect x="${953 + k * 10}" y="${cy - 6}" width="5" height="18" rx="2" fill="${radC}" opacity="${coOn ? 0.9 : 0.5}"/>`).join("")}`;
      }
      s += `<text x="895" y="362" text-anchor="middle" class="t-s">${st.house_c != null ? `średnio <tspan style="fill:#e0e6ed;font-weight:700">${num(st.house_c)}°</tspan> · ` : ""}zadana ${target}° · ${heatTxt}</text>`;
    } else {
      s += `<text x="895" y="132" text-anchor="middle" class="t-s">Dom</text>
        <text x="895" y="206" text-anchor="middle" class="t-big" style="fill:#e0e6ed">${room != null ? `${num(room)}°` : "—"}</text>
        <text x="895" y="230" text-anchor="middle" class="t-s">zadana ${target}°${st.humidity != null ? ` · ${num(st.humidity, 0)}%` : ""}</text>
        ${coOn ? `<rect x="846" y="246" width="98" height="54" rx="8" fill="${radC}" opacity=".35" filter="url(#hgGlow)"/>` : ""}
        <rect x="850" y="250" width="90" height="46" rx="6" fill="#0f172a" stroke="${radC}" stroke-width="2"/>
        ${[0, 1, 2, 3, 4, 5, 6].map((i) => `<rect x="${857 + i * 11.5}" y="255" width="7" height="36" rx="3" fill="${radC}" opacity="${coOn ? 0.9 : 0.5}"/>`).join("")}
        <text x="895" y="322" text-anchor="middle" class="t-s">${heatTxt}</text>`;
    }

    // outdoor
    if (ctl.t_out != null) {
      s += `<text x="1110" y="58" text-anchor="middle" class="t-s">na zewnątrz</text>
        <text x="1110" y="92" text-anchor="middle" class="t-big2">${num(ctl.t_out)}°</text>
        ${ctl.t_out_damped != null ? `<text x="1110" y="114" text-anchor="middle" class="t-s">średnio ${num(ctl.t_out_damped)}°</text>` : ""}`;
    }

    // hot-water pump and tank
    s += pumpSym(540, 260, dhwOn) + `<text x="562" y="256" class="t-s">pompa CWU</text>
      <text x="562" y="271" class="t-s ${dp.configured ? "" : "dim"}">${dp.configured ? (dhwOn ? `ładuje${pw((st.dhw_pump || {}).power_w)}` : "stoi") : "nieprzypisana"}</text>
      <text x="700" y="345" text-anchor="middle" class="t-h2">CWU ${num(c.dhw_l, 0)} L</text>
      <rect x="640" y="355" width="120" height="150" rx="26" fill="${tempColor(dhwT)}" opacity=".85" stroke="#64748b" stroke-width="2"/>
      <rect x="640" y="355" width="120" height="150" rx="26" fill="url(#hgShine)"/>`;
    s += `<path class="pc" d="${coil}" style="stroke-width:9"/><path class="pp" d="${coil}" stroke="${dhwOn ? feedC : "#475569"}" style="stroke-width:5"/>${dhwOn ? `<path class="pf" d="${coil}"/>` : ""}`;
    s += `<text x="776" y="412" class="t-big2" style="fill:#e0e6ed">${lbl("dhw")}</text>`;
    if (dhw) {
      s += `<text x="776" y="434" class="t-s">${num(dhw.charge_pct, 0)}% · ${num(dhw.energy_kwh)} kWh</text>
        <text x="776" y="452" class="t-s">do ${num(c.dhw_target_c, 0)}°</text>`;
    }
    return s + `</svg>`;
  }

  // ── 24 h chart (hero) ────────────────────────────────────────────────────
  // the fixed series plus one per storey (named after it) when the house has storeys
  _chartSeries() {
    const c = (this._data && this._data.config) || {};
    const n = Number(c.floors) || 1;
    const names = c.floor_names || [];
    const out = CHART_SERIES.map(([r, name, col]) => [r, r === "room" && n >= 2 ? `Pokój ref. (${names[c.room_floor || 0] || ""})` : name, col]);
    if (n >= 2) {
      const colors = ["#f472b6", "#fde68a", "#94a3b8"];
      for (let i = 0; i < n; i++) out.push([`floor${i}_temp`, names[i] || `Kondygnacja ${i + 1}`, colors[i]]);
    }
    return out;
  }

  _chartSvg(W, H, anim) {
    const c = this._chart;
    if (!c) return `<div class="muted" style="font-size:12px">Brak przypisanych czujników albo historii.</div>`;
    if (c.error) return `<div class="muted" style="font-size:12px">Historia niedostępna: ${esc(c.error)}</div>`;
    const ents = this._data.config.entities || {}, offs = this._data.config.offsets || {};
    const L = 34, R = 56, T = 10, B = 22;
    const series = [];
    let lo = Infinity, hi = -Infinity;
    for (const [role, name, color] of this._chartSeries()) {
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
    let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Temperatury z ostatniej doby">`;
    const step = hi - lo > 60 ? 20 : 10;
    for (let g = lo; g <= hi; g += step) {
      svg += `<line x1="${L}" x2="${W - R}" y1="${y(g)}" y2="${y(g)}" stroke="rgba(255,255,255,0.06)"/><text x="${L - 6}" y="${y(g) + 4}" text-anchor="end">${g}°</text>`;
    }
    for (let h = 0; h <= CHART_HOURS; h += 3) {
      const ts = c.start + h * 3600;
      const t = new Date(ts * 1000).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
      const anchor = h === 0 ? "start" : h === CHART_HOURS ? "end" : "middle";
      svg += `<text x="${x(ts)}" y="${H - 5}" text-anchor="${anchor}">${t}</text>`;
    }
    series.forEach((s, i) => {
      let dd = "";
      s.pts.forEach(([ts, v], k) => { dd += k === 0 ? `M${x(ts).toFixed(1)} ${y(v).toFixed(1)}` : ` H${x(ts).toFixed(1)} V${y(v).toFixed(1)}`; });
      const last = s.pts[s.pts.length - 1];
      const delay = anim ? ` style="animation-delay:${(i * 0.18).toFixed(2)}s"` : "";
      const endDelay = anim ? `animation-delay:${(1.4 + i * 0.18).toFixed(2)}s;` : "";
      svg += `<path d="${dd}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" pathLength="1" class="${anim ? "draw" : ""}"${delay}/>
        <circle cx="${x(last[0]).toFixed(1)}" cy="${y(last[1]).toFixed(1)}" r="3.5" fill="${s.color}" class="${anim ? "endp" : ""}" style="${endDelay}"/>
        <text x="${(x(last[0]) + 7).toFixed(1)}" y="${(y(last[1]) + 4).toFixed(1)}" style="${endDelay}fill:${s.color};font-weight:600" class="${anim ? "endp" : ""}">${num(last[1])}°</text>`;
    });
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

    const nFloors = Number(c.floors) || 1;
    const floorNames = c.floor_names || ["Parter", "1. piętro", "Poddasze"];
    const floorOpts = Object.fromEntries(floorNames.slice(0, nFloors).map((n, i) => [String(i), n]));
    html += `<div class="card" style="margin-top:14px"><h3>Dom — kondygnacje <small>temperatura domu piętro po piętrze</small></h3><div class="grid2">
      <label class="f">Liczba kondygnacji${sel("floors", { 1: "1 — cały dom jak jeden pokój", 2: "2", 3: "3 (najwyższa pod dachem)" }, String(nFloors))}</label>
      ${nFloors >= 2 ? `<label class="f">Pokój referencyjny jest na${sel("room_floor", floorOpts, String(c.room_floor || 0))}<span class="h">liczy się jako temperatura tej kondygnacji, gdy nie ma ona własnego czujnika</span></label>` : ""}
    </div>`;
    if (nFloors >= 2) {
      html += `<div class="grid2 floorsg" style="margin-top:12px">${floorNames.slice(0, nFloors).map((n, i) => `
        <label class="f">Kondygnacja ${i + 1} — nazwa<input name="floor_name_${i}" type="text" maxlength="24" value="${esc(n)}" ${dis}></label>
        <label class="f">${esc(n)} — powierzchnia (m²)<input name="floor_area_${i}" type="number" min="0" max="5000" step="1" value="${(c.floor_areas || [])[i] ?? ""}" placeholder="opcjonalnie" ${dis}></label>`).join("")}
      </div>
      <div class="hint" id="floorw">${this._floorWeightsHtml(c)}</div>
      <div class="hint">Czujniki kondygnacji wybierasz niżej, w sekcji „Czujniki”. Średnia domu jest ważona powierzchnią (bez powierzchni — zwykła średnia)
        i to ona trafia do modelu strat ciepła doradcy. Korekta krzywej grzewczej dalej działa według pokoju referencyjnego.</div>`;
    }
    html += `</div>`;

    html += `<div class="card" style="margin-top:14px"><h3>Czujniki <small>na żywo — ogrzej sondę w dłoni, a jej wartość zacznie rosnąć</small></h3>
      <div class="probe head"><div>Punkt pomiaru</div><div>Encja w Home Assistant</div><div style="text-align:right">Odczyt</div><div>Korekta (K)</div></div>`;
    for (const [role, meta] of Object.entries(d.roles || {})) {
      const fm = /^floor(\d)_/.exec(role);
      if (fm) {
        const i = Number(fm[1]);
        if (nFloors < 2 || i >= nFloors) continue;          // one storey: the reference room is the house
        html += this._probeRow(role, { ...meta, label: meta.label.replace(/^Kondygnacja \d/, floorNames[i]) }, c, dis);
        continue;
      }
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
      if (["valve_type", "emitters", "ent_dhw_pump_switch", "floors"].includes(e.target.name) || /^floor_name_/.test(e.target.name)) {
        this._renderBody();   // other fields / defaults, storey rows and their names
      }
    });
    form.addEventListener("input", () => {
      this._collect();
      const fw = root.getElementById("floorw");
      if (fw) fw.innerHTML = this._floorWeightsHtml(this._draft);
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
      else if (el.name === "floors" || el.name === "room_floor") c[el.name] = parseInt(el.value, 10) || 0;
      else if (el.name.startsWith("floor_name_")) {
        c.floor_names = [...(c.floor_names || ["Parter", "1. piętro", "Poddasze"])];
        c.floor_names[Number(el.name.slice(11))] = el.value;
      } else if (el.name.startsWith("floor_area_")) {
        c.floor_areas = [...(c.floor_areas || [null, null, null])];
        c.floor_areas[Number(el.name.slice(11))] = el.value === "" ? null : parseFloat(el.value);
      }
      else if (nums.includes(el.name)) c[el.name] = el.value === "" ? null : parseFloat(el.value);
    }
    if ((c.room_floor || 0) >= (c.floors || 1)) c.room_floor = 0;
  }

  // how each storey counts in the house mean — same rule as heating_model.floors_state
  _floorWeightsHtml(c) {
    const n = Math.min(Math.max(Number(c.floors) || 1, 1), 3);
    const names = (c.floor_names || ["Parter", "1. piętro", "Poddasze"]).slice(0, n);
    const areas = (c.floor_areas || []).slice(0, n).map((a) => (Number(a) > 0 ? Number(a) : null));
    if (areas.every((a) => a)) {
      const sum = areas.reduce((x, a) => x + a, 0);
      const fmt = (v) => v.toLocaleString("pl-PL", { maximumFractionDigits: 1 });
      return `Razem <b>${fmt(sum)} m²</b> · udział w średniej domu: ${names.map((nm, i) => `${esc(nm)} <b>${Math.round((areas[i] / sum) * 100)}%</b>`).join(" · ")}.
        Kondygnacja bez odczytu jest pomijana, a udziały pozostałych rosną proporcjonalnie.`;
    }
    return `Wpisz powierzchnię każdej kondygnacji, a średnia domu będzie ważona powierzchnią — teraz każda kondygnacja liczy się po równo.`;
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
