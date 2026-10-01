// Smarting HOME — "Dom / Biuro" panel.
// One professional overview of a home or office, built automatically from
// what Home Assistant already knows: people, cameras, security, rooms
// (areas — or categories when nothing is assigned to areas), climate,
// devices that need attention, weather and energy. No configuration needed;
// the Ustawienia view only hides cameras or sections.

const SECTION_IDS = ["people", "cameras", "security", "rooms", "health"];
const SECTION_LABELS = {
  people: "Osoby",
  cameras: "Kamery",
  security: "Bezpieczeństwo",
  rooms: "Pomieszczenia i urządzenia",
  health: "Stan urządzeń",
};
const TOGGLE_DOMAINS = new Set(["light", "switch", "fan", "input_boolean"]);
const ROOM_DOMAINS = ["light", "switch", "fan", "cover", "climate", "media_player", "lock", "vacuum", "input_boolean"];
const OPENING_CLASSES = new Set(["door", "window", "opening", "garage_door"]);
const ALERT_CLASSES = new Set(["smoke", "carbon_monoxide", "gas", "moisture", "safety", "problem", "tamper", "heat"]);
const MOTION_CLASSES = new Set(["motion", "occupancy", "presence"]);
// A device with only these (a player and its controls) is a cast/AirPlay target
const PLAYER_ONLY_DOMAINS = new Set(["media_player", "button", "select", "number", "text"]);
const WEATHER_PL = {
  "clear-night": "Bezchmurnie", sunny: "Słonecznie", partlycloudy: "Częściowe zachmurzenie",
  cloudy: "Pochmurno", fog: "Mgła", rainy: "Deszcz", pouring: "Ulewa", snowy: "Śnieg",
  "snowy-rainy": "Śnieg z deszczem", hail: "Grad", lightning: "Burza", "lightning-rainy": "Burza z deszczem",
  windy: "Wietrznie", "windy-variant": "Wietrznie", exceptional: "Ekstremalne warunki",
};
const ALARM_PL = {
  disarmed: "Rozbrojony", armed_home: "Uzbrojony (w domu)", armed_away: "Uzbrojony", armed_night: "Uzbrojony (noc)",
  armed_vacation: "Uzbrojony (urlop)", armed_custom_bypass: "Uzbrojony (własny)", arming: "Uzbrajanie…",
  disarming: "Rozbrajanie…", pending: "Oczekuje", triggered: "ALARM!",
};
const CATEGORY_LABELS = {
  light: "Oświetlenie", switch: "Przełączniki i gniazdka", fan: "Wentylacja", cover: "Rolety i bramy",
  climate: "Klimatyzacja i ogrzewanie", media_player: "Multimedia i głośniki", lock: "Zamki",
  vacuum: "Odkurzacze", input_boolean: "Przełączniki pomocnicze",
};

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function since(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 90) return "przed chwilą";
  if (s < 3600) return `${Math.round(s / 60)} min temu`;
  if (s < 86400) return `${Math.round(s / 3600)} h temu`;
  return `${Math.round(s / 86400)} dni temu`;
}

class SmartingHomeSitePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._view = "overview";
    this._settings = {};
    this._meter = {};
    this._sections = {};
    this._camUrls = {};
    this._renderPending = false;
    this._lastRender = 0;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._renderShell();
      this._loadSettings();
    }
    this._scheduleRender();
  }
  set narrow(n) { this._narrow = n; this._updateNarrow(); }
  set panel(p) { this._panel = p; }

  connectedCallback() {
    if (this._hass && !this.shadowRoot.querySelector(".wrap")) {
      this._renderShell();
      this._sections = {};
      this._scheduleRender();
    }
    this._clock = setInterval(() => this._tick(), 1000);
  }
  disconnectedCallback() {
    clearInterval(this._clock);
  }

  async _loadSettings() {
    try {
      const s = await this._hass.callWS({ type: "smartinghome/settings/get", keys: ["site", "meter"] });
      this._settings = (s && s.site) || {};
      this._meter = (s && s.meter) || {};
    } catch (e) {
      this._settings = {};
    }
    this._sections = {};
    this._renderNow();
  }
  _cfg(key, dflt) { return this._settings[key] === undefined ? dflt : this._settings[key]; }

  _tick() {
    const clock = this.shadowRoot.querySelector("#clock");
    if (clock) {
      const now = new Date();
      clock.innerHTML = `<b>${now.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" })}</b>
        <span>${now.toLocaleDateString("pl-PL", { weekday: "long", day: "numeric", month: "long" })}</span>`;
    }
    // Camera snapshots refresh (only visible, online cameras)
    const every = Math.max(3, Number(this._cfg("camera_refresh_s", 10)) || 10);
    this._camTick = (this._camTick || 0) + 1;
    if (this._view === "overview" && !document.hidden && this._camTick % every === 0) this._refreshCameras();
  }

  _scheduleRender() {
    if (this._renderPending) return;
    const wait = Math.max(0, 1000 - (Date.now() - this._lastRender));
    this._renderPending = true;
    setTimeout(() => { this._renderPending = false; this._renderNow(); }, wait);
  }

  // ── model ───────────────────────────────────────────────────────────
  _entity(id) { return (this._hass.entities || {})[id] || {}; }
  _device(id) { const e = this._entity(id); return e.device_id ? (this._hass.devices || {})[e.device_id] : null; }
  _areaId(id) { const e = this._entity(id); if (e.area_id) return e.area_id; const d = this._device(id); return d ? d.area_id : null; }
  _primary(id) { const e = this._entity(id); return !e.entity_category && !e.hidden; }
  _name(id) {
    const st = this._hass.states[id];
    return (st && st.attributes.friendly_name) || id;
  }
  _deviceName(id) {
    const d = this._device(id);
    return d ? (d.name_by_user || d.name || this._name(id)) : this._name(id);
  }
  _fmt(st) {
    try { if (this._hass.formatEntityState) return this._hass.formatEntityState(st); } catch (e) { /* fall through */ }
    const u = st.attributes.unit_of_measurement;
    return u ? `${st.state} ${u}` : st.state;
  }
  _ids(domain) { return Object.keys(this._hass.states).filter((id) => id.startsWith(domain + ".")); }
  _bySensorClass(domain, classes) {
    return this._ids(domain).filter((id) => this._primary(id) && classes.has(this._hass.states[id].attributes.device_class));
  }

  _cameras() {
    const hidden = new Set(this._cfg("hidden_cameras", []));
    const byDevice = new Map();
    this._ids("camera").filter((id) => this._primary(id)).forEach((id) => {
      const dev = this._entity(id).device_id || id;
      const score = (/high_res/.test(id) ? 0 : 2) + (/insecure|package/.test(id) ? 5 : 0) + (this._hass.states[id].state === "unavailable" ? 1 : 0);
      const cur = byDevice.get(dev);
      if (!cur || score < cur.score) byDevice.set(dev, { id, score, dev });
    });
    const motionByDevice = {};
    this._bySensorClass("binary_sensor", MOTION_CLASSES).forEach((id) => {
      const dev = this._entity(id).device_id;
      if (dev && this._hass.states[id].state === "on") motionByDevice[dev] = true;
    });
    const order = this._cfg("camera_order", []);
    return [...byDevice.values()]
      .filter((c) => !hidden.has(c.id))
      .map((c) => ({ ...c, name: this._deviceName(c.id), st: this._hass.states[c.id], motion: !!motionByDevice[c.dev] }))
      .sort((a, b) => {
        const oa = order.indexOf(a.id), ob = order.indexOf(b.id);
        if (oa !== ob) return (oa < 0 ? 999 : oa) - (ob < 0 ? 999 : ob);
        const ua = a.st.state === "unavailable", ub = b.st.state === "unavailable";
        if (ua !== ub) return ua ? 1 : -1;
        return a.name.localeCompare(b.name, "pl");
      });
  }

  _siteTitle() {
    const t = this._cfg("title", "");
    if (t) return t;
    return (this._meter.site_kind || "") === "business" ? "Biuro" : "Dom";
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
        .hl { display:flex; align-items:center; gap:12px; min-width:0; }
        .menu-btn { display:none; background:none; border:none; color:#a0aec0; font-size:20px; cursor:pointer; padding:4px 6px; border-radius:6px; }
        .menu-btn.show { display:block; }
        h1 { font-size:17px; font-weight:700; background:linear-gradient(135deg,#00d4ff,#00e676);
          -webkit-background-clip:text; -webkit-text-fill-color:transparent; white-space:nowrap; }
        .sub { font-size:11px; color:#8899aa; }
        #clock { display:flex; flex-direction:column; align-items:flex-end; font-size:11px; color:#8899aa; line-height:1.3; }
        #clock b { font-size:18px; color:#e0e6ed; font-variant-numeric:tabular-nums; }
        .hr { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
        .seg { display:flex; background:rgba(0,0,0,0.25); border-radius:8px; padding:2px; }
        .seg button, .btn { border:none; background:transparent; color:#8899aa; font:inherit; font-size:12px; padding:6px 11px; border-radius:6px; cursor:pointer; }
        .seg button.on { background:rgba(0,212,255,0.12); color:#00d4ff; }
        .btn { background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.08); color:#cbd5e1; text-decoration:none; display:inline-flex; align-items:center; gap:6px; }
        .btn:hover { background:rgba(0,212,255,0.12); color:#00d4ff; }
        .btn.primary { background:linear-gradient(135deg,#00d4ff,#00e676); color:#0a1628; font-weight:700; border:none; }
        .content { padding:16px 20px 40px; max-width:1500px; margin:0 auto; display:flex; flex-direction:column; gap:14px; }
        .chips { display:flex; gap:8px; flex-wrap:wrap; }
        .chip { display:inline-flex; align-items:center; gap:7px; padding:8px 12px; border-radius:999px; font-size:12px;
          background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08); color:#cbd5e1; cursor:default; }
        .chip b { color:#fff; font-variant-numeric:tabular-nums; }
        .chip.ok { border-color:rgba(46,204,113,0.35); } .chip.ok i { background:#2ecc71; }
        .chip.warn { border-color:rgba(247,183,49,0.45); background:rgba(247,183,49,0.08); } .chip.warn i { background:#f7b731; }
        .chip.bad { border-color:rgba(231,76,60,0.55); background:rgba(231,76,60,0.12); } .chip.bad i { background:#e74c3c; }
        .chip i { width:8px; height:8px; border-radius:50%; background:#64748b; display:inline-block; }
        .grid { display:grid; gap:14px; }
        .top { grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr)); }
        .card { background:rgba(20,30,48,0.45); border:1px solid rgba(255,255,255,0.07); border-radius:14px; padding:16px; min-width:0; }
        .card h3 { font-size:13px; font-weight:600; color:#a0aec0; letter-spacing:.02em; margin-bottom:12px;
          display:flex; justify-content:space-between; align-items:baseline; gap:4px 8px; flex-wrap:wrap; }
        .card h3 small { font-weight:400; color:#64748b; font-size:11px; }
        .people { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:10px; }
        .person { display:flex; align-items:center; gap:10px; padding:8px; border-radius:12px; background:rgba(255,255,255,0.03); cursor:pointer; }
        .avatar { width:42px; height:42px; border-radius:50%; background:#1e293b center/cover no-repeat; flex:none;
          display:flex; align-items:center; justify-content:center; font-weight:700; color:#00d4ff; position:relative; }
        .avatar::after { content:""; position:absolute; right:-1px; bottom:-1px; width:12px; height:12px; border-radius:50%;
          border:2px solid #111d35; background:var(--dot,#64748b); }
        .person .n { font-size:13px; font-weight:600; } .person .s { font-size:11px; color:#8899aa; }
        .weather { display:flex; align-items:center; gap:16px; }
        .weather .t { font-size:40px; font-weight:700; font-variant-numeric:tabular-nums; }
        .weather .t small { font-size:18px; color:#8899aa; }
        .kv { display:grid; grid-template-columns:auto auto; gap:4px 14px; font-size:12px; color:#8899aa; }
        .kv b { color:#e0e6ed; font-weight:500; text-align:right; }
        .cams { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,${280}px),1fr)); gap:12px; }
        .cam { position:relative; border-radius:12px; overflow:hidden; background:#05080f; aspect-ratio:16/9; cursor:pointer;
          border:1px solid rgba(255,255,255,0.06); }
        .cam img { width:100%; height:100%; object-fit:cover; display:block; }
        .cam .lbl { position:absolute; left:0; right:0; bottom:0; padding:22px 10px 8px; font-size:12px; font-weight:600;
          background:linear-gradient(transparent, rgba(0,0,0,0.75)); display:flex; justify-content:space-between; gap:8px; }
        .cam .badges { position:absolute; top:8px; left:8px; display:flex; gap:6px; }
        .badge { font-size:10px; font-weight:700; padding:3px 7px; border-radius:6px; background:rgba(0,0,0,0.6); letter-spacing:.04em; }
        .badge.rec { color:#ff6b6b; } .badge.motion { color:#0a1628; background:#f7b731; }
        .cam.off { display:flex; align-items:center; justify-content:center; color:#64748b; font-size:12px; cursor:pointer; }
        .cam.off .lbl { background:none; color:#8899aa; font-weight:500; }
        details summary { cursor:pointer; font-size:12px; color:#8899aa; margin-top:12px; list-style:none; }
        details summary::-webkit-details-marker { display:none; }
        .rows { display:flex; flex-direction:column; gap:6px; font-size:13px; }
        .row { display:flex; justify-content:space-between; align-items:center; gap:10px; padding:6px 8px; border-radius:8px; cursor:pointer; }
        .row:hover { background:rgba(255,255,255,0.04); }
        .row .k { color:#cbd5e1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .row .v { color:#8899aa; white-space:nowrap; font-variant-numeric:tabular-nums; }
        .row .v.bad { color:#ff6b6b; font-weight:600; } .row .v.warn { color:#f7b731; } .row .v.ok { color:#2ecc71; }
        .empty { font-size:12px; color:#64748b; padding:4px 8px; }
        .rooms { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,300px),1fr)); gap:14px; }
        .room-meta { display:flex; gap:10px; font-size:12px; color:#8899aa; }
        .tiles { display:grid; grid-template-columns:repeat(auto-fill,minmax(130px,1fr)); gap:8px; }
        .tile { border:1px solid rgba(255,255,255,0.08); background:rgba(255,255,255,0.03); border-radius:10px; padding:9px 10px;
          cursor:pointer; text-align:left; font:inherit; color:#cbd5e1; display:flex; flex-direction:column; gap:3px; min-width:0; }
        .tile .tn { font-size:12px; font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .tile .ts { font-size:11px; color:#8899aa; }
        .tile.on { background:rgba(247,183,49,0.12); border-color:rgba(247,183,49,0.45); }
        .tile.on .ts { color:#f7b731; }
        .tile.unav { opacity:.45; }
        .shortcuts { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
        form .grid2 { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; }
        label.f { display:flex; flex-direction:column; gap:5px; font-size:12px; color:#a0aec0; }
        label.c { display:flex; align-items:center; gap:8px; font-size:13px; color:#cbd5e1; padding:4px 0; }
        input, select { padding:8px 10px; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12);
          border-radius:8px; color:#fff; font:inherit; font-size:13px; }
        input[type=checkbox] { width:16px; height:16px; accent-color:#00d4ff; }
        select option { background:#1e293b; }
        .actions { display:flex; gap:8px; justify-content:flex-end; margin-top:16px; }
        @media (max-width:600px) {
          .header { padding:8px 12px; } .content { padding:12px 12px 32px; }
          #clock { display:none; }
        }
      </style>
      <div class="wrap">
        <div class="header">
          <div class="hl">
            <button class="menu-btn" title="Menu">☰</button>
            <div><h1 id="title">Dom</h1><div class="sub" id="sub"></div></div>
          </div>
          <div class="hr">
            <div id="clock"></div>
            <div class="seg" id="views">
              <button data-view="overview" class="on">Przegląd</button>
              <button data-view="settings">Ustawienia</button>
            </div>
          </div>
        </div>
        <div class="content" id="body"></div>
      </div>`;
    const root = this.shadowRoot;
    root.querySelector(".menu-btn").addEventListener("click", () =>
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })));
    root.querySelectorAll("#views button").forEach((b) => b.addEventListener("click", () => {
      this._view = b.dataset.view;
      this._sections = {};
      this._renderNow();
      root.querySelector(".wrap").scrollIntoView({ block: "start" });
    }));
    root.querySelector("#body").addEventListener("click", (ev) => this._onClick(ev));
    this._updateNarrow();
    this._tick();
  }

  _updateNarrow() {
    const btn = this.shadowRoot && this.shadowRoot.querySelector(".menu-btn");
    if (btn) btn.classList.toggle("show", !!this._narrow);
  }

  _onClick(ev) {
    const t = ev.target.closest("[data-toggle],[data-more],[data-nav]");
    if (!t || this._view !== "overview") return;
    if (t.dataset.nav) {
      history.pushState(null, "", t.dataset.nav);
      window.dispatchEvent(new CustomEvent("location-changed", { bubbles: true, composed: true }));
      return;
    }
    if (t.dataset.toggle) {
      const id = t.dataset.toggle;
      const domain = id.split(".")[0];
      if (TOGGLE_DOMAINS.has(domain)) this._hass.callService("homeassistant", "toggle", { entity_id: id });
      else if (domain === "cover") this._hass.callService("cover", "toggle", { entity_id: id });
      else if (domain === "media_player") this._hass.callService("media_player", "media_play_pause", { entity_id: id });
      return;
    }
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: t.dataset.more }, bubbles: true, composed: true }));
  }

  // ── render ──────────────────────────────────────────────────────────
  _renderNow() {
    if (!this._hass) return;
    this._lastRender = Date.now();
    const root = this.shadowRoot;
    const body = root.querySelector("#body");
    if (!body) return;
    root.querySelectorAll("#views button").forEach((b) => b.classList.toggle("on", b.dataset.view === this._view));
    root.querySelector("#title").textContent = this._siteTitle();
    root.querySelector("#sub").textContent = this._hass.config.location_name || "";
    if (this._view === "settings") {
      if (!this._settingsShown) {
        body.innerHTML = this._settingsHtml();
        this._bindSettings();
        this._settingsShown = true;
      }
      return;
    }
    this._settingsShown = false;
    const hidden = new Set(this._cfg("hidden_sections", []));
    const parts = [
      ["status", this._statusHtml()],
      ["top", `<div class="grid top">${this._peopleHtml()}${this._weatherHtml()}</div>`],
      ...SECTION_IDS.filter((s) => s !== "people" && !hidden.has(s)).map((s) => [s, this[`_${s}Html`]()]),
    ];
    if (hidden.has("people")) parts[1][1] = `<div class="grid top">${this._weatherHtml()}</div>`;
    // Patch per section — camera <img> elements survive unrelated updates
    const keep = new Set(parts.map(([k]) => k));
    [...body.children].forEach((el) => { if (!keep.has(el.dataset.sec)) el.remove(); });
    parts.forEach(([key, html], idx) => {
      let el = body.querySelector(`:scope > [data-sec="${key}"]`);
      if (!el) {
        el = document.createElement("div");
        el.dataset.sec = key;
        body.insertBefore(el, body.children[idx] || null);
      }
      if (this._sections[key] !== html) {
        el.innerHTML = html;
        el.style.display = html ? "" : "none";
        this._sections[key] = html;
        if (key === "cameras") this._applyCameraUrls();
      }
    });
  }

  _statusHtml() {
    const S = this._hass.states;
    const chips = [];
    const persons = this._ids("person").filter((id) => this._primary(id));
    if (persons.length) {
      const present = persons.filter((id) => this._isHome(S[id])).length;
      chips.push(`<span class="chip ${present ? "ok" : ""}"><i></i>Na miejscu <b>${present}/${persons.length}</b></span>`);
    }
    this._ids("alarm_control_panel").filter((id) => this._primary(id)).forEach((id) => {
      const s = S[id].state;
      const cls = s === "triggered" ? "bad" : s.startsWith("armed") ? "ok" : "";
      chips.push(`<span class="chip ${cls}" data-more="${esc(id)}" style="cursor:pointer" title="${esc(this._deviceName(id))}"><i></i>Alarm: <b>${esc(ALARM_PL[s] || s)}</b></span>`);
    });
    const open = this._bySensorClass("binary_sensor", OPENING_CLASSES).filter((id) => S[id].state === "on");
    if (this._bySensorClass("binary_sensor", OPENING_CLASSES).length) {
      chips.push(`<span class="chip ${open.length ? "warn" : "ok"}"><i></i>Otwarte drzwi/okna <b>${open.length}</b></span>`);
    }
    const alerts = this._bySensorClass("binary_sensor", ALERT_CLASSES).filter((id) => S[id].state === "on");
    if (alerts.length) chips.push(`<span class="chip bad"><i></i>Alarmy czujników <b>${alerts.length}</b></span>`);
    const lights = this._ids("light").filter((id) => this._primary(id));
    if (lights.length) {
      const on = lights.filter((id) => S[id].state === "on").length;
      chips.push(`<span class="chip ${on ? "warn" : ""}"><i></i>Światła <b>${on}/${lights.length}</b></span>`);
    }
    const cams = this._cameras();
    if (cams.length) {
      const online = cams.filter((c) => c.st.state !== "unavailable").length;
      chips.push(`<span class="chip ${online === cams.length ? "ok" : "warn"}"><i></i>Kamery online <b>${online}/${cams.length}</b></span>`);
      const motion = cams.filter((c) => c.motion).length;
      if (motion) chips.push(`<span class="chip warn"><i></i>Ruch teraz <b>${motion}</b></span>`);
    }
    const power = this._meter.power_entity && S[this._meter.power_entity];
    if (power && !isNaN(parseFloat(power.state))) {
      chips.push(`<span class="chip" data-nav="/smartinghome-energia" style="cursor:pointer"><i style="background:#00d4ff"></i>Pobór <b>${esc(this._fmt(power))}</b></span>`);
    }
    const health = this._health();
    if (health.updates.length) chips.push(`<span class="chip warn"><i></i>Aktualizacje <b>${health.updates.length}</b></span>`);
    if (health.lowBattery.length) chips.push(`<span class="chip warn"><i></i>Słabe baterie <b>${health.lowBattery.length}</b></span>`);
    return `<div class="chips">${chips.join("")}</div>`;
  }

  _isHome(st) {
    if (!st) return false;
    if (st.state === "home") return true;
    const home = this._hass.states["zone.home"];
    return !!home && st.state === home.attributes.friendly_name;
  }

  _peopleHtml() {
    const S = this._hass.states;
    const persons = this._ids("person").filter((id) => this._primary(id));
    if (!persons.length) return "";
    const items = persons.map((id) => {
      const st = S[id];
      const home = this._isHome(st);
      const zone = st.state === "not_home" ? "Poza obiektem" : st.state === "unknown" ? "Nieznana lokalizacja" : home ? "Na miejscu" : st.state;
      const pic = st.attributes.entity_picture;
      const initials = (st.attributes.friendly_name || id).split(" ").map((p) => p[0]).join("").slice(0, 2).toUpperCase();
      return `<div class="person" data-more="${esc(id)}">
        <div class="avatar" style="--dot:${home ? "#2ecc71" : "#64748b"};${pic ? `background-image:url('${esc(pic)}')` : ""}">${pic ? "" : esc(initials)}</div>
        <div style="min-width:0"><div class="n">${esc(st.attributes.friendly_name || id)}</div>
        <div class="s">${esc(zone)} · ${esc(since(st.last_changed))}</div></div></div>`;
    }).join("");
    return `<div class="card"><h3>Osoby <small>obecność wg aplikacji mobilnej / lokalizacji</small></h3><div class="people">${items}</div></div>`;
  }

  _weatherHtml() {
    const S = this._hass.states;
    const pref = this._cfg("weather_entity", "");
    const id = (pref && S[pref]) ? pref : this._ids("weather").find((w) => this._primary(w) && S[w].state !== "unavailable");
    const shortcuts = `<div class="shortcuts">
      <span class="btn" data-nav="/smartinghome-energia">⚡ Energia i koszty</span>
      ${this._hasPvPanel() ? `<span class="btn" data-nav="/smartinghome">☀️ Smarting HOME</span>` : ""}
      ${this._cameras().length ? `<span class="btn" data-more="${esc(this._cameras()[0].id)}">📹 Podgląd kamery</span>` : ""}
    </div>`;
    if (!id) return `<div class="card"><h3>Skróty</h3>${shortcuts}</div>`;
    const st = S[id], a = st.attributes;
    const unit = a.temperature_unit || "°C";
    return `<div class="card"><h3>Pogoda <small>${esc(a.friendly_name || id)}</small></h3>
      <div class="weather" data-more="${esc(id)}" style="cursor:pointer">
        <div class="t">${a.temperature !== undefined ? esc(Math.round(a.temperature)) : "—"}<small>${esc(unit)}</small></div>
        <div style="flex:1"><div style="font-size:14px;font-weight:600;margin-bottom:6px">${esc(WEATHER_PL[st.state] || st.state)}</div>
          <div class="kv">
            ${a.humidity !== undefined ? `<span>Wilgotność</span><b>${esc(Math.round(a.humidity))}%</b>` : ""}
            ${a.wind_speed !== undefined ? `<span>Wiatr</span><b>${esc(Math.round(a.wind_speed))} ${esc(a.wind_speed_unit || "km/h")}</b>` : ""}
            ${a.pressure !== undefined ? `<span>Ciśnienie</span><b>${esc(Math.round(a.pressure))} ${esc(a.pressure_unit || "hPa")}</b>` : ""}
          </div></div>
      </div>${shortcuts}</div>`;
  }

  _hasPvPanel() {
    const panels = this._hass.panels || {};
    return !!panels.smartinghome;
  }

  _camerasHtml() {
    const cams = this._cameras();
    if (!cams.length) return "";
    const online = cams.filter((c) => c.st.state !== "unavailable");
    const offline = cams.filter((c) => c.st.state === "unavailable");
    const tile = (c) => `<div class="cam" data-more="${esc(c.id)}" data-cam="${esc(c.id)}">
        <img alt="" decoding="async">
        <div class="badges">${c.st.state === "recording" ? `<span class="badge rec">● REC</span>` : ""}${c.motion ? `<span class="badge motion">RUCH</span>` : ""}</div>
        <div class="lbl"><span>${esc(c.name)}</span></div></div>`;
    const off = offline.map((c) => `<div class="cam off" data-more="${esc(c.id)}"><span>offline</span>
        <div class="lbl"><span>${esc(c.name)}</span><span>${esc(since(c.st.last_changed))}</span></div></div>`).join("");
    return `<div class="card"><h3>Kamery <small>${online.length} online${offline.length ? ` · ${offline.length} offline` : ""} · kliknij, aby zobaczyć na żywo</small></h3>
      <div class="cams">${online.map(tile).join("") || `<div class="empty">Żadna kamera nie jest teraz dostępna.</div>`}</div>
      ${offline.length ? `<details><summary>▸ Kamery offline (${offline.length})</summary><div class="cams" style="margin-top:10px">${off}</div></details>` : ""}</div>`;
  }

  _camUrl(id) {
    const st = this._hass.states[id];
    const pic = st && st.attributes.entity_picture;
    if (!pic) return "";
    return `${pic}${pic.includes("?") ? "&" : "?"}t=${Date.now()}`;
  }
  _refreshCameras() {
    this.shadowRoot.querySelectorAll("[data-cam]").forEach((el) => {
      const id = el.dataset.cam;
      const url = this._camUrl(id);
      if (!url) return;
      const img = new Image();
      img.onload = () => {
        this._camUrls[id] = url;
        const target = el.querySelector("img");
        if (target) target.src = url;
      };
      img.src = url;
    });
  }
  _applyCameraUrls() {
    this.shadowRoot.querySelectorAll("[data-cam]").forEach((el) => {
      const id = el.dataset.cam;
      const img = el.querySelector("img");
      if (!img) return;
      img.src = this._camUrls[id] || this._camUrl(id);
      if (!this._camUrls[id]) this._camUrls[id] = img.src;
    });
  }

  _securityHtml() {
    const S = this._hass.states;
    const rows = [];
    this._ids("alarm_control_panel").filter((id) => this._primary(id)).forEach((id) => {
      const s = S[id].state;
      rows.push(`<div class="row" data-more="${esc(id)}"><span class="k">🛡️ ${esc(this._deviceName(id))}</span>
        <span class="v ${s === "triggered" ? "bad" : s.startsWith("armed") ? "ok" : ""}">${esc(ALARM_PL[s] || s)}</span></div>`);
    });
    this._ids("lock").filter((id) => this._primary(id)).forEach((id) => {
      const s = S[id].state;
      rows.push(`<div class="row" data-more="${esc(id)}"><span class="k">🔒 ${esc(this._name(id))}</span>
        <span class="v ${s === "unlocked" ? "warn" : "ok"}">${s === "locked" ? "Zamknięty" : s === "unlocked" ? "Otwarty" : esc(s)}</span></div>`);
    });
    this._bySensorClass("binary_sensor", ALERT_CLASSES).filter((id) => S[id].state === "on").forEach((id) => {
      rows.push(`<div class="row" data-more="${esc(id)}"><span class="k">🚨 ${esc(this._name(id))}</span><span class="v bad">wykryto</span></div>`);
    });
    this._bySensorClass("binary_sensor", OPENING_CLASSES).filter((id) => S[id].state === "on").forEach((id) => {
      rows.push(`<div class="row" data-more="${esc(id)}"><span class="k">🚪 ${esc(this._name(id))}</span><span class="v warn">otwarte · ${esc(since(S[id].last_changed))}</span></div>`);
    });
    const motion = this._bySensorClass("binary_sensor", MOTION_CLASSES);
    const recent = motion
      .map((id) => ({ id, st: S[id] }))
      .filter((m) => m.st.state !== "unavailable")
      .sort((a, b) => new Date(b.st.last_changed) - new Date(a.st.last_changed))
      .slice(0, 6);
    if (!rows.length && !recent.length) return "";
    const motionRows = recent.map((m) => `<div class="row" data-more="${esc(m.id)}"><span class="k">${m.st.state === "on" ? "🟡" : "⚪"} ${esc(this._deviceName(m.id))}</span>
      <span class="v ${m.st.state === "on" ? "warn" : ""}">${m.st.state === "on" ? "ruch teraz" : "ostatnio " + esc(since(m.st.last_changed))}</span></div>`).join("");
    return `<div class="grid top">
      <div class="card"><h3>Bezpieczeństwo</h3><div class="rows">${rows.join("") || `<div class="empty">Brak alarmów, wszystko zamknięte.</div>`}</div></div>
      ${recent.length ? `<div class="card"><h3>Ruch <small>ostatnie wykrycia</small></h3><div class="rows">${motionRows}</div></div>` : ""}
    </div>`;
  }

  _roomsHtml() {
    const S = this._hass.states;
    const areas = this._hass.areas || {};
    const hiddenAreas = new Set(this._cfg("hidden_areas", []));
    const cameraDevices = new Set(this._ids("camera").map((id) => this._entity(id).device_id).filter(Boolean));
    const entities = ROOM_DOMAINS.flatMap((d) => this._ids(d))
      .filter((id) => this._primary(id))
      .filter((id) => {
        const e = this._entity(id);
        if (e.platform === "smartinghome") return false; // autopilot switches live in the PV panel
        if (id.startsWith("media_player.") && e.device_id && cameraDevices.has(e.device_id)) return false; // camera speakers
        if (id.startsWith("media_player.") && S[id].state === "unavailable") return false;
        return true;
      });
    const groups = new Map();
    const useAreas = entities.some((id) => this._areaId(id));
    entities.forEach((id) => {
      const area = this._areaId(id);
      const key = area && areas[area] ? `area:${area}` : useAreas ? "area:_none" : `cat:${id.split(".")[0]}`;
      if (key.startsWith("area:") && hiddenAreas.has(key.slice(5))) return;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(id);
    });
    // Climate per area: temperature / humidity sensors
    const climate = {};
    this._ids("sensor").filter((id) => this._primary(id)).forEach((id) => {
      const dc = S[id].attributes.device_class;
      if (dc !== "temperature" && dc !== "humidity") return;
      const area = this._areaId(id);
      if (!area) return;
      const v = parseFloat(S[id].state);
      if (isNaN(v)) return;
      (climate[area] = climate[area] || { temperature: [], humidity: [] })[dc].push(v);
      if (!groups.has(`area:${area}`) && areas[area] && !hiddenAreas.has(area)) groups.set(`area:${area}`, []);
    });
    if (!groups.size) return "";
    const tile = (id, dupes) => {
      const st = S[id];
      const d = id.split(".")[0];
      const on = ["on", "open", "playing", "heat", "cool", "heat_cool", "auto", "unlocked", "cleaning"].includes(st.state);
      const unav = st.state === "unavailable";
      const action = TOGGLE_DOMAINS.has(d) || d === "cover" ? "toggle" : "more";
      let label = this._fmt(st);
      if (d === "climate") {
        const cur = st.attributes.current_temperature, tgt = st.attributes.temperature;
        label = `${cur !== undefined ? cur + "°" : ""}${tgt !== undefined ? " → " + tgt + "°" : ""} · ${this._fmt(st)}`;
      }
      if (d === "media_player" && st.attributes.media_title) label = `▶ ${st.attributes.media_title}`;
      return `<button class="tile ${on ? "on" : ""} ${unav ? "unav" : ""}" data-${action === "toggle" ? "toggle" : "more"}="${esc(id)}" title="${esc(this._name(id))}">
        <span class="tn">${esc(dupes.has(this._shortName(id)) ? this._name(id) : this._shortName(id))}</span><span class="ts">${esc(label)}</span></button>`;
    };
    const cards = [...groups.entries()].sort(([a], [b]) => {
      if (a === "area:_none") return 1;
      if (b === "area:_none") return -1;
      return a.localeCompare(b, "pl");
    }).map(([key, ids]) => {
      let title, meta = "";
      if (key.startsWith("area:")) {
        const aid = key.slice(5);
        title = aid === "_none" ? "Bez przypisanego pomieszczenia" : (areas[aid] && areas[aid].name) || aid;
        const c = climate[aid];
        if (c) {
          const avg = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
          const t = avg(c.temperature), h = avg(c.humidity);
          meta = `<span class="room-meta">${t !== null ? `<span>🌡 ${t.toFixed(1)}°C</span>` : ""}${h !== null ? `<span>💧 ${Math.round(h)}%</span>` : ""}</span>`;
        }
      } else {
        title = CATEGORY_LABELS[key.slice(4)] || key.slice(4);
      }
      const onCount = ids.filter((id) => ["on", "open", "playing"].includes(S[id].state)).length;
      const seen = new Map();
      ids.forEach((id) => { const n = this._shortName(id); seen.set(n, (seen.get(n) || 0) + 1); });
      const dupes = new Set([...seen].filter(([, c]) => c > 1).map(([n]) => n));
      return `<div class="card"><h3>${esc(title)} ${meta || `<small>${onCount ? `${onCount} włączone · ` : ""}${ids.length} urządzeń</small>`}</h3>
        ${ids.length ? `<div class="tiles">${ids.sort((a, b) => this._shortName(a).localeCompare(this._shortName(b), "pl")).map((id) => tile(id, dupes)).join("")}</div>` : `<div class="empty">Tylko czujniki klimatu.</div>`}</div>`;
    });
    const hint = useAreas ? "" : `<small>urządzenia nie mają przypisanych pomieszczeń — przypisz je w HA (Ustawienia → Obszary), a panel pogrupuje je po pomieszczeniach</small>`;
    return `<div class="card" style="background:transparent;border:none;padding:0"><h3 style="margin-bottom:10px">${useAreas ? "Pomieszczenia" : "Urządzenia"} ${hint}</h3>
      <div class="rooms">${cards.join("")}</div></div>`;
  }

  _shortName(id) {
    const name = this._name(id);
    const dev = this._device(id);
    const devName = dev && (dev.name_by_user || dev.name);
    if (devName && name.startsWith(devName + " ") && name.length > devName.length + 1) return name.slice(devName.length + 1);
    return name;
  }

  _health() {
    const S = this._hass.states;
    const updates = this._ids("update").filter((id) => S[id].state === "on");
    const lowBattery = this._ids("sensor").filter((id) => {
      const st = S[id];
      if (st.attributes.device_class !== "battery" || st.attributes.unit_of_measurement !== "%") return false;
      const v = parseFloat(st.state);
      return !isNaN(v) && v <= Number(this._cfg("battery_low_pct", 20));
    });
    const devices = new Map();
    Object.keys(S).forEach((id) => {
      if (!this._primary(id) || S[id].state !== "unavailable") return;
      const e = this._entity(id);
      if (!e.device_id) return;
      const d = (this._hass.devices || {})[e.device_id];
      if (!d || d.disabled_by) return;
      devices.set(e.device_id, { name: d.name_by_user || d.name || id, id });
    });
    // A device is "offline" only when all of its primary entities are unavailable.
    // Media players (AirPlay/Cast targets of laptops, phones) come and go — not a fault.
    const nonPlayer = new Set();
    Object.keys(S).forEach((id) => {
      const e = this._entity(id);
      if (!e.device_id || !devices.has(e.device_id) || !this._primary(id)) return;
      if (S[id].state !== "unavailable") devices.delete(e.device_id);
      if (!PLAYER_ONLY_DOMAINS.has(id.split(".")[0])) nonPlayer.add(e.device_id);
    });
    [...devices.keys()].forEach((dev) => { if (!nonPlayer.has(dev)) devices.delete(dev); });
    return { updates, lowBattery, offline: [...devices.values()] };
  }

  _healthHtml() {
    const S = this._hass.states;
    const h = this._health();
    if (!h.updates.length && !h.lowBattery.length && !h.offline.length) {
      return `<div class="card"><h3>Stan urządzeń</h3><div class="empty">Wszystkie urządzenia działają, baterie w porządku, brak aktualizacji.</div></div>`;
    }
    const col = (title, rows) => rows.length ? `<div class="card"><h3>${title}</h3><div class="rows">${rows.join("")}</div></div>` : "";
    return `<div class="grid top">
      ${col(`Niedostępne urządzenia <small>${h.offline.length}</small>`, h.offline.slice(0, 12).map((d) =>
        `<div class="row" data-more="${esc(d.id)}"><span class="k">${esc(d.name)}</span><span class="v bad">offline · ${esc(since(S[d.id].last_changed))}</span></div>`))}
      ${col(`Słabe baterie <small>≤ ${esc(this._cfg("battery_low_pct", 20))}%</small>`, h.lowBattery.map((id) =>
        `<div class="row" data-more="${esc(id)}"><span class="k">${esc(this._name(id))}</span><span class="v warn">${esc(this._fmt(S[id]))}</span></div>`))}
      ${col(`Aktualizacje <small>${h.updates.length}</small>`, h.updates.slice(0, 12).map((id) =>
        `<div class="row" data-more="${esc(id)}"><span class="k">${esc(S[id].attributes.title || this._name(id))}</span><span class="v">${esc(S[id].attributes.latest_version || "")}</span></div>`))}
    </div>`;
  }

  // ── settings ────────────────────────────────────────────────────────
  _settingsHtml() {
    const hiddenCams = new Set(this._cfg("hidden_cameras", []));
    const hiddenSections = new Set(this._cfg("hidden_sections", []));
    const hiddenAreas = new Set(this._cfg("hidden_areas", []));
    const allCams = (() => { const saved = this._settings.hidden_cameras; this._settings.hidden_cameras = []; const c = this._cameras(); this._settings.hidden_cameras = saved; return c; })();
    const weathers = this._ids("weather");
    return `<form id="f"><div class="grid top">
      <div class="card"><h3>Panel</h3>
        <div class="grid2">
          <label class="f">Nazwa panelu <span style="font-size:11px;color:#64748b">w menu bocznym zmieni się po restarcie HA</span><input name="title" value="${esc(this._cfg("title", ""))}" placeholder="${esc(this._siteTitle())} (automatycznie)"></label>
          <label class="f">Pogoda <select name="weather_entity"><option value="">automatycznie</option>${weathers.map((w) => `<option value="${esc(w)}"${w === this._cfg("weather_entity", "") ? " selected" : ""}>${esc(this._name(w))}</option>`).join("")}</select></label>
          <label class="f">Odświeżanie kamer (s) <input type="number" min="3" max="120" name="camera_refresh_s" value="${esc(this._cfg("camera_refresh_s", 10))}"></label>
          <label class="f">Próg słabej baterii (%) <input type="number" min="1" max="100" name="battery_low_pct" value="${esc(this._cfg("battery_low_pct", 20))}"></label>
        </div>
        <h3 style="margin-top:16px">Sekcje</h3>
        ${SECTION_IDS.map((s) => `<label class="c"><input type="checkbox" data-section="${s}"${hiddenSections.has(s) ? "" : " checked"}> ${SECTION_LABELS[s]}</label>`).join("")}
      </div>
      <div class="card"><h3>Kamery <small>odznacz, aby ukryć</small></h3>
        ${allCams.map((c) => `<label class="c"><input type="checkbox" data-cam="${esc(c.id)}"${hiddenCams.has(c.id) ? "" : " checked"}> ${esc(c.name)} <span style="color:#64748b;font-size:11px">${c.st.state === "unavailable" ? "offline" : ""}</span></label>`).join("") || `<div class="empty">Brak kamer.</div>`}
      </div>
      <div class="card"><h3>Pomieszczenia <small>odznacz, aby ukryć</small></h3>
        ${Object.values(this._hass.areas || {}).map((a) => `<label class="c"><input type="checkbox" data-area="${esc(a.area_id)}"${hiddenAreas.has(a.area_id) ? "" : " checked"}> ${esc(a.name)}</label>`).join("") || `<div class="empty">Brak obszarów w HA.</div>`}
      </div>
    </div>
    <div class="actions"><button type="submit" class="btn primary">Zapisz</button></div></form>`;
  }

  _bindSettings() {
    const f = this.shadowRoot.querySelector("#f");
    if (!f) return;
    f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const unchecked = (attr) => [...f.querySelectorAll(`input[data-${attr}]`)].filter((i) => !i.checked).map((i) => i.dataset[attr]);
      const site = {
        ...this._settings,
        title: f.elements.title.value.trim(),
        weather_entity: f.elements.weather_entity.value,
        camera_refresh_s: Number(f.elements.camera_refresh_s.value) || 10,
        battery_low_pct: Number(f.elements.battery_low_pct.value) || 20,
        hidden_sections: unchecked("section"),
        hidden_cameras: unchecked("cam"),
        hidden_areas: unchecked("area"),
      };
      const btn = f.querySelector("button[type=submit]");
      btn.disabled = true;
      btn.textContent = "Zapisywanie…";
      try {
        await this._hass.callWS({ type: "smartinghome/settings/update", settings: { site } });
        this._settings = site;
        this._view = "overview";
        this._sections = {};
        this._settingsShown = false;
        this._renderNow();
      } catch (e) {
        btn.disabled = false;
        btn.textContent = "Zapisz";
        alert("Nie udało się zapisać: " + ((e && e.message) || e));
      }
    });
  }
}

if (!customElements.get("smartinghome-site-panel")) {
  customElements.define("smartinghome-site-panel", SmartingHomeSitePanel);
}
