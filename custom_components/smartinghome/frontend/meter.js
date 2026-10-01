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
    root.querySelector("#refresh").addEventListener("click", () => this._load());
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
        <b>Tauron AMIplus</b> (konto w eLiczniku TAURON Dystrybucji) albo miernik typu <b>Shelly Pro 3EM</b>.
        Potem wybierz go w <b>Ustawieniach</b> tego panelu.</div>`;
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
    const delta = cm.forecast_total && pm.total ? (cm.forecast_total - pm.total) / pm.total * 100 : null;

    const kpis = `
      <div class="grid kpis">
        <div class="card kpi" id="kpi-live">
          <div class="lbl">Moc teraz</div>
          <div class="val" id="live-val">—</div>
          <div class="note" id="live-note">${s.power_entity ? "" : "Dodaj czujnik mocy w Ustawieniach"}</div>
        </div>
        <div class="card kpi">
          <div class="lbl">Ten miesiąc</div>
          <div class="val">${this._num(cm.kwh, 0)}<small>kWh</small></div>
          <div class="note">${costHidden ? "uzupełnij ceny" : `${this._zl(cm.total)} z opłatami stałymi`} · ${cm.days_with_data || 0}/${cm.days || "—"} dni</div>
        </div>
        <div class="card kpi">
          <div class="lbl">Prognoza rachunku</div>
          <div class="val">${costHidden ? "—" : this._zl(cm.forecast_total, 0)}</div>
          <div class="note">${pm.total && !costHidden ? `poprzedni miesiąc ${this._zl(pm.total, 0)}` : "za cały miesiąc"}
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
        ${this._peakCard(d)}
        ${this._qualityCard()}
        ${pm.kwh ? this._costCard(pm, "Poprzedni miesiąc", costHidden) : ""}
      </div>
      <div class="foot">
        Źródło: ${esc(d.source.name)} (${esc(d.source.id)}) · dane do ${d.data_until ? esc(d.data_until.replace("T", " ")) : "—"}<br>
        Kwoty ${this._showGross ? "brutto (z VAT 23%)" : "netto"} · ceny z Ustawień panelu (domyślnie taryfy TAURON 2026) ·
        bez energii biernej i rozliczeń korygujących. Moc szczytowa to średnia godzinowa — licznik rozlicza maksimum 15-minutowe.
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
      <div class="row"><span class="k">Opłaty stałe (miesiąc)</span><span class="v">${this._zl(m.fixed)}</span></div>
      ${fixedRows}
      <div class="row total"><span class="k" style="color:#e0e6ed">Razem</span><span class="v">${this._zl(m.total)}</span></div>
      ${m.kwh ? `<div class="hint">Średnio <b>${this._zl(m.total / m.kwh)}</b> za 1 kWh wraz z opłatami stałymi.</div>` : ""}`;
    return `<div class="card"><h3>${esc(title)} <small>${this._num(m.kwh, 1)} kWh · ${m.days_with_data || 0} dni z danymi</small></h3>
      <div class="stack">${bar}</div><div class="rows">${zoneRows}${money}</div></div>`;
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
    const peak = (d.peak && (d.peak.current_month || d.peak.previous_month)) || null;
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
    const pf = this._stateNum(s.pf_entity);
    if (!s.pf_entity && !this._isBusiness) return ""; // reactive energy is billed in C tariffs only
    if (!s.pf_entity) {
      return `<div class="card"><h3>Jakość energii · energia bierna</h3>
        <div class="hint">Licznik w eLiczniku nie pokazuje współczynnika mocy na bieżąco, a w taryfach C operator
        dolicza <b>energię bierną pojemnościową</b> (zasilacze, UPS-y, LED, kondensatory w rozdzielnicy) —
        potrafi to być nawet kilkadziesiąt procent rachunku.<br><br>
        Miernik typu <b>Shelly Pro 3EM</b> w rozdzielnicy pokaże moc i <b>cos φ</b> na każdej fazie na żywo.
        Dodaj jego czujnik współczynnika mocy w Ustawieniach.</div></div>`;
    }
    const v = pf ? Math.abs(pf.v > 1.5 ? pf.v / 100 : pf.v) : null;
    const good = v !== null && v >= 0.93;
    return `<div class="card"><h3>Jakość energii · energia bierna <small>${esc(s.pf_entity)}</small></h3>
      <div class="rows">
        <div class="row"><span class="k">Współczynnik mocy (cos φ) teraz</span>
          <span class="v" id="pf-val"><b class="${good ? "down" : "up"}">${v !== null ? this._num(v, 2) : "—"}</b></span></div>
      </div>
      <div class="hint">${v === null ? "Czujnik nie ma teraz wartości."
        : good ? "Współczynnik mocy w normie (≥ 0,93)."
        : "Niski współczynnik mocy — przy małym obciążeniu zwykle oznacza energię bierną pojemnościową, za którą operator nalicza opłaty. Elektryk może dobrać dławik kompensacyjny albo odłączyć zbędną baterię kondensatorów."}</div></div>`;
  }

  _updateLive() {
    if (this._view !== "overview" || !this._data) return;
    const root = this.shadowRoot;
    const valEl = root.querySelector("#live-val");
    const noteEl = root.querySelector("#live-note");
    const s = this._settings;
    if (valEl && s.power_entity) {
      const p = this._stateNum(s.power_entity);
      if (p) {
        const w = /kw/i.test(p.unit) ? p.v * 1000 : p.v;
        valEl.innerHTML = Math.abs(w) >= 1000
          ? `${this._num(w / 1000, 2)}<small>kW</small>`
          : `${this._num(w, 0)}<small>W</small>`;
        if (noteEl) noteEl.textContent = p.st.attributes.friendly_name || s.power_entity;
      } else {
        valEl.textContent = "—";
        if (noteEl) noteEl.textContent = `${s.power_entity} — brak wartości`;
      }
    }
    const pfEl = root.querySelector("#pf-val");
    if (pfEl && s.pf_entity) {
      const pf = this._stateNum(s.pf_entity);
      const v = pf ? Math.abs(pf.v > 1.5 ? pf.v / 100 : pf.v) : null;
      pfEl.innerHTML = `<b class="${v !== null && v >= 0.93 ? "down" : "up"}">${v !== null ? this._num(v, 2) : "—"}</b>`;
    }
  }

  // ── charts (inline SVG) ──────────────────────────────────────────────
  _dailyChart(daily, tariff, W) {
    if (!daily.length) return `<div class="hint">Brak danych dziennych.</div>`;
    const zones = (tariff && tariff.zones) || ["flat"];
    const H = 220, padL = 34, padB = 22, padT = 8;
    const max = Math.max(...daily.map((d) => d.kwh), 0.1);
    const step = (W - padL) / daily.length;
    const bw = Math.max(3, step * 0.7);
    const y = (v) => padT + (H - padT - padB) * (1 - v / max);
    let bars = "";
    daily.forEach((d, i) => {
      let acc = 0;
      const x = padL + i * step + (step - bw) / 2;
      zones.forEach((z) => {
        const v = (d.zones && d.zones[z]) || 0;
        if (v <= 0) return;
        const y1 = y(acc + v), y0 = y(acc);
        bars += `<rect x="${x.toFixed(1)}" y="${y1.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(0.5, y0 - y1).toFixed(1)}" fill="${ZONE_COLORS[z] || "#00d4ff"}" opacity="${d.free ? 0.55 : 0.9}"><title>${esc(d.date)} · ${esc(this._zoneLabel(z))}: ${this._num(v, 1)} kWh</title></rect>`;
        acc += v;
      });
      const dd = d.date.slice(8, 10);
      const every = Math.ceil(daily.length / Math.max(4, Math.floor(W / 34)));
      if (i % every === 0) {
        bars += `<text x="${(x + bw / 2).toFixed(1)}" y="${H - 6}" text-anchor="middle"${d.free ? ' style="fill:#a78bfa"' : ""}>${dd}</text>`;
      }
      bars += `<rect x="${x.toFixed(1)}" y="${padT}" width="${bw.toFixed(1)}" height="${H - padT - padB}" fill="transparent"><title>${esc(d.date)}${d.free ? " (dzień wolny)" : ""}: ${this._num(d.kwh, 1)} kWh${tariff && !tariff.needs_input ? " · " + this._zl(d.variable) : ""}</title></rect>`;
    });
    const grid = [0.5, 1].map((f) => `<line x1="${padL}" x2="${W}" y1="${y(max * f)}" y2="${y(max * f)}" stroke="rgba(255,255,255,0.06)"/><text x="${padL - 4}" y="${y(max * f) + 3}" text-anchor="end">${this._num(max * f, max * f < 10 ? 1 : 0)}</text>`).join("");
    const legend = zones.map((z) => `<span><i style="background:${ZONE_COLORS[z] || "#00d4ff"}"></i>${esc(this._zoneLabel(z))}</span>`).join("")
      + `<span><i style="background:#a78bfa"></i>dzień wolny (bledszy słupek)</span><span>kWh / dzień</span>`;
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
    const sources = (d.sources || []).map((x) => `<option value="${esc(x.id)}"${x.id === s.energy_stat ? " selected" : ""}>${esc(x.name)} (${esc(x.id)})</option>`).join("");
    const isPower = (st) => st.attributes.device_class === "power" || /^(k?W)$/.test(st.attributes.unit_of_measurement || "");
    const isPf = (st) => st.attributes.device_class === "power_factor" || /power_factor|cos/i.test(st.entity_id);
    const zoneRows = (t.zones || []).map((z) => `
      <tr><td><span class="dot" style="display:inline-block;background:${ZONE_COLORS[z]}"></span> ${esc(this._zoneLabel(z))}</td>
      <td><input type="number" step="0.0001" min="0" data-part="energy" data-key="${z}" value="${esc(val("energy", z))}"></td>
      <td><input type="number" step="0.0001" min="0" data-part="dist" data-key="${z}" value="${esc(val("dist", z))}"></td></tr>`).join("");
    const fixedRows = Object.keys(t.fixed || {}).map((k) => `
      <label class="f">${esc(this._fixedLabel(k))} <span class="h">zł netto / miesiąc</span>
      <input type="number" step="0.01" min="0" data-part="fixed" data-key="${k}" value="${esc(val("fixed", k))}"></label>`).join("");
    const contractRate = pr.contract_rate !== undefined ? pr.contract_rate : t.contract_rate;
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
            <label class="f" style="grid-column:1/-1">Dane licznika (statystyka kWh)
              <span class="h">Tauron AMIplus zapisuje je jako tauron_amiplus:…_consumption</span>
              <select name="energy_stat"><option value="">automatycznie</option>${sources}</select></label>
            <label class="f">Moc umowna <span class="h">kW, z faktury</span>
              <input type="number" step="0.1" min="0" name="contract_kw" value="${esc(s.contract_kw ?? "")}"></label>
            <label class="f">Próg stałego obciążenia <span class="h">W — powyżej panel ostrzega</span>
              <input type="number" step="10" min="0" name="baseload_alert_w" value="${esc(s.baseload_alert_w ?? 150)}"></label>
          </div>
        </div>
        <div class="card">
          <h3>Pomiar na żywo <small>opcjonalnie</small></h3>
          <div class="grid2">
            <label class="f" style="grid-column:1/-1">Czujnik mocy z sieci <span class="h">np. Shelly Pro 3EM — moc całkowita (W)</span>
              <select name="power_entity">${this._entityOptions(s.power_entity, isPower)}</select></label>
            <label class="f" style="grid-column:1/-1">Współczynnik mocy (cos φ) <span class="h">pokazuje problem z energią bierną</span>
              <select name="pf_entity">${this._entityOptions(s.pf_entity, isPf)}</select></label>
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

  _bindSettings() {
    const f = this.shadowRoot.querySelector("#f");
    if (!f) return;
    const read = () => {
      const s = this._draft;
      ["site_kind", "tariff", "energy_stat", "power_entity", "pf_entity"].forEach((n) => { s[n] = f.elements[n].value; });
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
    f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const s = read();
      const btn = f.querySelector("button[type=submit]");
      btn.disabled = true;
      btn.textContent = "Zapisywanie…";
      try {
        await this._hass.callWS({ type: "smartinghome/settings/update", settings: { meter: s } });
        this._draft = null;
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
