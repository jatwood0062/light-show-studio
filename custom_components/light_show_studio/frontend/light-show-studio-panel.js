// Light Show Studio sidebar app: Play / Lights / Add shows / Setup in one page. Ships with light_show_studio.
// Reuses the two cards, so light-show-studio-card.js and light-show-studio-player-card.js must be loaded too.
// Custom element: light-show-studio-panel (HA sets hass, narrow, route, panel on it).

const TABS = [
  ["play", "Play", "M8 5v14l11-7z"],
  ["lights", "Lights", "M12 2a7 7 0 0 0-4 12.7V17a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-2.3A7 7 0 0 0 12 2zM9 20h6v1a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1z"],
  ["studio", "Studio", "M3 17v2h6v-2H3zM3 5v2h10V5H3zm10 16v-2h8v-2h-8v-2h-2v6h2zM7 9v2H3v2h4v2h2V9H7zm14 4v-2H11v2h10zm-6-4h2V7h4V5h-4V3h-2v6z"],
  ["add", "Add shows", "M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6z"],
  ["setup", "Setup", "M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z"],
];
const INTEGRATION_PATH = "/config/integrations/integration/light_show_studio";
const STORE_KEY = "light_show_studio_panel_tab";
const SEL_KEY = "light_show_studio_panel_light";
const SWATCHES = ["#ff0000", "#ff5000", "#ffc800", "#00ff00", "#00ffc8", "#0040ff", "#8200ff", "#ff1478", "#ffb43c", "#ffffff"];
const SEASONAL = /Christmas|Candy|Halloween|Valentine|Patrick|Easter|July 4th|Thanksgiving|Hanukkah|Witches|Classic Multicolour|Warm White/;
const SHOW = "Light show";
const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;

const svg = (d, size = 24) =>
  `<svg viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true"><path fill="currentColor" d="${d}"/></svg>`;

const store = (key) => ({
  get() {
    try {
      return localStorage.getItem(key);
    } catch (e) {
      return null;
    }
  },
  set(v) {
    try {
      localStorage.setItem(key, v);
    } catch (e) {
      /* storage blocked: it just isn't remembered */
    }
  },
});
const stored = store(STORE_KEY);
const storedLight = store(SEL_KEY);

class LightShowStudioPanel extends HTMLElement {
  constructor() {
    super();
    this._tab = null;
    this._cards = new Map(); // entity id -> light card
    this._strands = null; // from light_show_studio/strands
    this._editing = null; // entry id whose inline editor is open
    this._studioSel = storedLight.get(); // entry id (or entity id from a deep link) shown in the Studio tab
    try {
      this._deepLink = new URLSearchParams(location.search).get("studio");
    } catch (e) {
      this._deepLink = null;
    }
    if (this._deepLink) this._studioSel = this._deepLink;
    this._studioCards = new Map();
  }

  connectedCallback() {
    this._startPolling();
  }

  disconnectedCallback() {
    clearInterval(this._poll);
    this._poll = null;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._update();
    this._startPolling();
  }

  set narrow(v) {
    this._narrow = !!v;
    this._root?.classList.toggle("narrow", this._narrow);
    if (this._menu) this._menu.hidden = !this._narrow;
  }

  set route(v) {
    this._route = v;
  }

  set panel(v) {
    this._panel = v;
  }

  get hass() {
    return this._hass;
  }

  // --- data --------------------------------------------------------------
  _lights() {
    const known = (this._strands || []).map((s) => s.entity_id).filter((id) => id && this._hass.states[id]);
    // an unavailable entity has no attributes, so offline strands come from the strands list
    const ids = Object.keys(this._hass.states).filter((id) => {
      const a = this._hass.states[id].attributes;
      return id.startsWith("light.") && ((a.bulbs && (a.effect_list || []).includes("Light show")) || known.includes(id));
    });
    return ids;
  }

  // --- layout ------------------------------------------------------------
  _build() {
    this._built = true;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { display: flex; flex-direction: column; height: 100%; min-height: 100vh; box-sizing: border-box;
                background: var(--primary-background-color); color: var(--primary-text-color);
                font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
        @supports (height: 100dvh) { :host { min-height: 100dvh; } }
        * { box-sizing: border-box; }
        [hidden] { display: none !important; }
        button { font: inherit; color: inherit; cursor: pointer; border: none; background: none; padding: 0;
                 -webkit-tap-highlight-color: transparent; }
        :focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
        header { display: flex; align-items: center; gap: 4px; min-height: 56px; padding: 0 8px 0 12px; flex: none;
                 background: var(--app-header-background-color, var(--primary-color));
                 color: var(--app-header-text-color, var(--text-primary-color, #fff)); }
        .narrow header { padding-left: 4px; }
        h1 { font-size: 20px; font-weight: 400; margin: 0 16px 0 4px; white-space: nowrap; }
        .menu { width: 48px; height: 48px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; }
        .menu:hover { background: rgba(255,255,255,.15); }
        nav { display: flex; }
        nav button { position: relative; display: inline-flex; align-items: center; gap: 6px; padding: 0 16px; height: 56px;
                     font-size: 14px; font-weight: 500; text-transform: uppercase; letter-spacing: .04em; opacity: .7; }
        nav button[aria-selected=true] { opacity: 1; box-shadow: inset 0 -3px 0 currentColor; }
        nav button:hover { opacity: 1; }
        .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--error-color, #db4437); display: inline-block; }
        main { flex: 1; overflow: auto; padding: 16px; width: 100%; max-width: 900px; margin: 0 auto; }
        section > * + * { margin-top: 16px; }
        .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr)); }
        .grid > * { min-width: 0; margin: 0 !important; }
        .box { background: var(--card-background-color, #fff); border-radius: var(--ha-card-border-radius, 12px);
               border: 1px solid var(--divider-color); padding: 16px; line-height: 1.5; }
        .box h2 { font-size: 16px; font-weight: 500; margin: 0 0 8px; }
        .box ol { margin: 0; padding-left: 22px; }
        .box li + li { margin-top: 6px; }
        .muted { color: var(--secondary-text-color); font-size: 13px; }
        .btn { font-size: 14px; font-weight: 500; padding: 8px 16px; min-height: 40px; border-radius: 20px;
               background: var(--primary-color); color: var(--text-primary-color, #fff); }
        .btn.alt { background: none; color: var(--primary-color); border: 1px solid var(--divider-color); }
        .check { list-style: none; padding: 0; margin: 0; }
        .check li { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--divider-color); }
        .check li:last-child { border-bottom: none; }
        .mark { flex: none; width: 28px; height: 28px; border-radius: 50%; display: inline-flex; align-items: center;
                justify-content: center; font-weight: 700; color: #fff; background: var(--error-color, #db4437); }
        .mark.ok { background: var(--success-color, #43a047); }
        .check .t { flex: 1; min-width: 0; }
        .check .t small { display: block; color: var(--secondary-text-color); font-size: 12px; }
        code { background: var(--secondary-background-color, rgba(127,127,127,.15)); padding: 1px 6px; border-radius: 4px; }
        .empty { text-align: center; padding: 28px 16px; color: var(--secondary-text-color); }
        .empty .btn { margin-top: 12px; }
        .btn.sm { min-height: 36px; padding: 6px 14px; }
        .btn:disabled { opacity: .5; cursor: default; }
        /* strands list */
        .strands { padding: 4px 16px; }
        .strand { padding: 12px 0; border-bottom: 1px solid var(--divider-color); display: flex; flex-wrap: wrap;
                  align-items: center; gap: 8px 12px; }
        .strand:last-child { border-bottom: none; }
        .strand .info { flex: 1 1 260px; min-width: 0; }
        .strand .name { display: flex; align-items: center; gap: 8px; font-weight: 500; min-width: 0; }
        .strand .name span.n { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .sdot { flex: none; width: 10px; height: 10px; border-radius: 50%; background: var(--disabled-text-color, #9e9e9e); }
        .sdot.on { background: var(--success-color, #43a047); }
        .sdot.off { background: var(--error-color, #db4437); }
        .state { font-size: 12px; color: var(--secondary-text-color); font-weight: 400; }
        .state.off { color: var(--error-color, #db4437); font-weight: 500; }
        .meta { font-size: 13px; color: var(--secondary-text-color); display: flex; flex-wrap: wrap; gap: 2px 12px; margin-top: 2px; }
        .ip { font-family: var(--code-font-family, ui-monospace, monospace); color: inherit; font-size: 13px;
              text-decoration: underline dotted; padding: 2px 0; min-height: 24px; }
        .acts { display: flex; gap: 8px; flex: none; margin-left: auto; }
        .warn { flex: 1 0 100%; font-size: 13px; color: var(--warning-color, #b36b00); }
        .serr { flex: 1 0 100%; font-size: 13px; color: var(--error-color, #db4437); }
        .edit { flex: 1 0 100%; display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: flex-end; }
        .edit label { display: flex; flex-direction: column; font-size: 12px; color: var(--secondary-text-color); gap: 2px; }
        .edit input { font: inherit; font-size: 14px; padding: 6px 8px; min-height: 40px; width: 110px; max-width: 100%;
                      border: 1px solid var(--divider-color); border-radius: 8px; background: var(--card-background-color, #fff);
                      color: var(--primary-text-color); }
        .edit label.nm { flex: 1 1 180px; } .edit label.nm input { width: 100%; }
        .edit .free { flex: 1 0 100%; font-size: 12px; color: var(--secondary-text-color); }
        .edit .free button { color: var(--primary-color); text-decoration: underline; font-size: 12px; }
        .xl { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; }
        .xl h2 { flex: 1 0 100%; }
        .xl .line { flex: 1 1 240px; min-width: 0; overflow-wrap: anywhere; font-size: 14px; }
        .addrow { display: flex; }
        @media (max-width: 600px) {
          .strands { padding: 4px 12px; }
          .acts { margin-left: 0; flex: 1 0 100%; }
          .acts .btn { flex: 1; }
        }
        .strand[data-open] { cursor: pointer; }
        .strand[data-open]:hover .n { text-decoration: underline; }
        /* studio tab */
        .st-top { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; }
        .st-top label { flex: 1 1 240px; min-width: 0; display: flex; flex-direction: column; gap: 2px; font-size: 12px;
                        color: var(--secondary-text-color); }
        select, .st input[type=text], .st input[type=number] { font: inherit; font-size: 14px; padding: 6px 8px; min-height: 40px;
                 border: 1px solid var(--divider-color); border-radius: 8px; background: var(--card-background-color, #fff);
                 color: var(--primary-text-color); max-width: 100%; min-width: 0; }
        .st-top select { width: 100%; }
        .st-top .sdot { margin-top: 18px; }
        .st-top .state { margin-top: 18px; }
        .st-live { min-width: 0; }
        .st-live > * { display: block; margin: 0; }
        .st-ctl > * + *, .st-fx > * + * { margin-top: 14px; }
        .st-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; }
        .st-mode { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; justify-content: space-between; }
        .st-mode .ml { font-weight: 500; min-width: 0; overflow-wrap: anywhere; }
        .pw { display: inline-flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 500; padding: 8px 16px;
              min-height: 40px; border-radius: 20px; border: 1px solid var(--divider-color); }
        .pw[aria-checked=true] { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
        .bri { flex: 1 1 200px; min-width: 0; display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--secondary-text-color); }
        .bri input { flex: 1; min-width: 0; height: 40px; accent-color: var(--primary-color); }
        .bri .bv { width: 3.2em; text-align: right; }
        .sws { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
        .sw { width: 36px; height: 36px; border-radius: 50%; border: 2px solid transparent; box-shadow: inset 0 0 0 1px #0003; }
        .sw.sel { border-color: var(--primary-text-color); }
        .st input[type=color] { width: 40px; height: 36px; padding: 0; border: 2px solid var(--divider-color); border-radius: 8px;
                                background: none; cursor: pointer; }
        .grp { margin-top: 12px; }
        .grp h3 { font-size: 13px; font-weight: 500; margin: 0 0 6px; color: var(--secondary-text-color); }
        .chips { display: flex; flex-wrap: wrap; gap: 8px; }
        .chip { font-size: 14px; padding: 6px 14px; min-height: 40px; border-radius: 20px; border: 1px solid var(--divider-color);
                background: var(--card-background-color, #fff); max-width: 100%; overflow-wrap: anywhere; }
        .chip[aria-pressed=true] { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
        .chip.show { font-weight: 500; }
        .ctl-off { opacity: .5; pointer-events: none; }
        .st-set .edit input { width: 120px; }
        .st-set .edit label.ip input { width: 150px; }
        .st-set .live { flex: 1 0 100%; font-size: 13px; color: var(--secondary-text-color); }
        .st-set .btnrow { flex: 1 0 100%; display: flex; gap: 8px; flex-wrap: wrap; }
        /* phones: tabs become a bottom bar */
        .narrow nav { position: fixed; left: 0; right: 0; bottom: 0; z-index: 5; background: var(--card-background-color, #fff);
                      color: var(--secondary-text-color); border-top: 1px solid var(--divider-color);
                      padding-bottom: env(safe-area-inset-bottom); }
        .narrow nav button { flex: 1; flex-direction: column; justify-content: center; gap: 2px; height: 60px; padding: 0 2px;
                             font-size: 11px; text-transform: none; letter-spacing: 0; opacity: 1; min-width: 0; }
        .narrow nav button[aria-selected=true] { color: var(--primary-color); box-shadow: inset 0 3px 0 var(--primary-color); }
        .narrow nav .dot { position: absolute; top: 8px; left: calc(50% + 8px); }
        .narrow main { padding: 12px 12px calc(76px + env(safe-area-inset-bottom)); }
              .btn.rm { color: var(--error-color, #db4437); border-color: var(--error-color, #db4437); margin-left: auto; }
      </style>
      <div class="root">
        <header>
          <button class="menu" aria-label="Open the sidebar menu" hidden>${svg("M3 6h18v2H3zm0 5h18v2H3zm0 5h18v2H3z")}</button>
          <h1>Light Shows</h1>
          <nav role="tablist" aria-label="Light Shows sections"></nav>
        </header>
        <main>
          <section id="p-play" role="tabpanel"></section>
          <section id="p-lights" role="tabpanel"></section>
          <section id="p-studio" role="tabpanel" class="st"></section>
          <section id="p-add" role="tabpanel">
            <div class="box">
              <h2>Add a show</h2>
              <ol>
                <li>In xLights open your sequence and render it (it saves a <code>.fseq</code> next to the <code>.xsq</code>).</li>
                <li>Add the <code>.fseq</code> and the song here — same name, like <code>Thriller.fseq</code> and <code>Thriller.m4a</code>.</li>
                <li>Press Play.</li>
              </ol>
            </div>
          </section>
          <section id="p-setup" role="tabpanel"></section>
        </main>
      </div>`;
    this._root = root.querySelector(".root");
    this._root.classList.toggle("narrow", !!this._narrow);
    this._root.addEventListener("light-show-studio-open", (e) => {
      const hit = this._resolveStrand(e.detail?.entity);
      if (hit) this._openStudio(hit);
    });
    this._menu = root.querySelector(".menu");
    this._menu.hidden = !this._narrow;
    this._menu.onclick = () => this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
    this._sections = Object.fromEntries(TABS.map(([id]) => [id, root.querySelector(`#p-${id}`)]));

    const nav = root.querySelector("nav");
    this._tabBtns = {};
    for (const [id, label, path] of TABS) {
      const b = document.createElement("button");
      b.setAttribute("role", "tab");
      b.setAttribute("aria-controls", `p-${id}`);
      b.innerHTML = `${svg(path, 22)}<span>${label}</span>`;
      b.onclick = () => this._select(id, true);
      nav.append(b);
      this._tabBtns[id] = b;
    }
    this._dot = Object.assign(document.createElement("span"), { className: "dot", title: "Setup isn't finished" });
    this._dot.setAttribute("aria-label", "Setup isn't finished");
    this._tabBtns.setup.append(this._dot);

    this._player = this._card("light-show-studio-player-card", { mode: "player" });
    this._sections.play.append(this._player);
    this._adder = this._card("light-show-studio-player-card", { mode: "upload" });
    this._sections.add.append(this._adder);
    this._sections.lights.innerHTML = `
      <div class="box strands" role="list" aria-label="Light strands" hidden></div>
      <div class="box xl" hidden></div>
      <div class="addrow" hidden></div>
      <div class="grid"></div>`;
    const q = (c) => this._sections.lights.querySelector(c);
    this._strandBox = q(".strands");
    this._xlBox = q(".xl");
    this._addRow = q(".addrow");
    const add = Object.assign(document.createElement("button"), { className: "btn alt", textContent: "Add a light" });
    add.onclick = () => this._navigate(INTEGRATION_PATH);
    this._addRow.append(add);
    this._lightsGrid = q(".grid");
    this._buildSetup();
    this._buildStudio();
  }

  // a card element that works whether or not its script has loaded yet
  _card(tag, config) {
    const el = document.createElement(tag);
    const ready = () => {
      try {
        el.setConfig(config);
        el._ready = true;
        if (this._hass) el.hass = this._hass;
      } catch (err) {
        el.textContent = err.message;
      }
    };
    if (customElements.get(tag)) ready();
    else customElements.whenDefined(tag).then(ready);
    return el;
  }

  _feed(el) {
    if (this._hass && el._ready) el.hass = this._hass;
  }

  _select(id, user) {
    this._tab = id;
    for (const [t, s] of Object.entries(this._sections)) s.hidden = t !== id;
    for (const [t, b] of Object.entries(this._tabBtns)) b.setAttribute("aria-selected", String(t === id));
    if (user) stored.set(id);
    if (id === "lights" || id === "studio") this._loadStrands();
    if (id === "studio") this._renderStudio();
  }

  // --- strands list --------------------------------------------------------
  _startPolling() {
    if (this._poll || !this._hass || !this.isConnected) return;
    this._loadStrands();
    this._poll = setInterval(() => {
      if (document.hidden) return;
      if ((this._tab === "lights" && !this._editing) || this._tab === "studio") this._loadStrands();
    }, 15000);
  }

  async _loadStrands() {
    if (this._loading || !this._hass) return;
    this._loading = true;
    try {
      const r = await this._hass.callWS({ type: "light_show_studio/strands" });
      this._strands = Array.isArray(r?.strands) ? r.strands : [];
      this._info = { ddp: r?.ddp_port || 4048, max: r?.max_pixels || 2040 };
      this._strandsError = "";
    } catch (err) {
      this._strandsError = (typeof err === "string" ? err : err?.message) || "Couldn't load the strands.";
    }
    this._loading = false;
    if (this._deepLink && this._strands) {
      const hit = this._resolveStrand(this._deepLink);
      this._deepLink = null;
      if (hit) {
        this._studioSel = hit.entry_id;
        this._select("studio", false);
      }
    }
    this._renderStrands();
    this._renderStudio();
    if (this._hass) this._update();
  }

  _isAdmin() {
    return !!this._hass?.user?.is_admin;
  }

  static _range(s) {
    return [s.start_pixel, s.start_pixel + s.bulbs - 1];
  }

  _overlaps(s) {
    const [a0, a1] = LightShowStudioPanel._range(s);
    const out = [];
    for (const o of this._strands) {
      if (o.entry_id === s.entry_id) continue;
      const [b0, b1] = LightShowStudioPanel._range(o);
      const lo = Math.max(a0, b0), hi = Math.min(a1, b1);
      if (lo <= hi) out.push(`Overlaps ${o.title}: pixels ${lo}\u2013${hi}`);
    }
    return out;
  }

  _nextFree(except) {
    let end = 0;
    for (const o of this._strands) if (o !== except) end = Math.max(end, LightShowStudioPanel._range(o)[1]);
    return end + 1;
  }

  static _ago(iso) {
    const t = iso ? Date.parse(iso) : NaN;
    if (isNaN(t)) return "";
    const m = Math.max(0, Math.round((Date.now() - t) / 60000));
    if (m < 1) return "just now";
    if (m < 60) return `${m} min ago`;
    const h = Math.round(m / 60);
    if (h < 48) return `${h} h ago`;
    return `${Math.round(h / 24)} days ago`;
  }

  _renderStrands() {
    const box = this._strandBox;
    if (!box) return;
    const list = this._strands;
    if (!list || (!list.length && !this._strandsError)) {
      box.hidden = true;
      this._xlBox.hidden = true;
      this._addRow.hidden = !list;
      return;
    }
    if (this._editing) return; // don't wipe a form that's being typed in
    box.hidden = false;
    box.textContent = "";
    if (this._strandsError) box.append(Object.assign(document.createElement("div"), { className: "serr", textContent: this._strandsError }));
    const sorted = [...list].sort((a, b) => a.start_pixel - b.start_pixel);
    for (const s of sorted) box.append(this._strandRow(s));
    this._renderXl(sorted);
    this._addRow.hidden = false;
  }

  _el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  _strandRow(s) {
    const row = this._el("div", "strand");
    row.setAttribute("role", "listitem");
    row.dataset.open = "1";
    row.tabIndex = 0;
    row.title = "Open in Studio";
    row.onclick = (e) => {
      if (!e.target.closest("button, input, form, a, select")) this._openStudio(s);
    };
    row.onkeydown = (e) => e.target === row && e.key === "Enter" && this._openStudio(s);
    const [p0, p1] = LightShowStudioPanel._range(s);
    const state = !s.loaded ? ["", "Not loaded"] : s.online === true ? ["on", "Online"]
      : s.online === false ? ["off", "Offline" + (s.last_seen ? ` \u00b7 last seen ${LightShowStudioPanel._ago(s.last_seen)}` : " \u00b7 never seen")]
      : ["", "checking\u2026"];
    const info = this._el("div", "info");
    const name = this._el("div", "name");
    const dot = this._el("span", "sdot " + state[0]);
    dot.setAttribute("role", "img");
    dot.setAttribute("aria-label", state[1]);
    name.append(dot, this._el("span", "n", s.title), this._el("span", "state " + state[0], state[1]));
    const meta = this._el("div", "meta");
    const kind = `${{ twinkly: "Twinkly", wled: "WLED" }[s.kind] || "Govee"}${s.model ? " " + s.model : ""}`;
    const ip = this._el("button", "ip", s.host || "");
    ip.title = "Tap to copy";
    ip.onclick = () => this._copy(s.host, ip, s.host);
    meta.append(
      this._el("span", "", kind),
      ip,
      this._el("span", "", `Pixels ${p0}\u2013${p1} \u00b7 Channels ${(p0 - 1) * 3 + 1}\u2013${(p1) * 3}`)
    );
    info.append(name, meta);
    const acts = this._el("div", "acts");
    const idb = this._el("button", "btn alt sm", "Identify");
    idb.disabled = !s.loaded || s.online === false;
    idb.onclick = () => this._identify(s, idb, row);
    acts.append(idb);
    if (this._isAdmin()) {
      const eb = this._el("button", "btn alt sm", "Edit");
      eb.onclick = () => this._openEdit(s, row);
      acts.append(eb);
    }
    row.append(info, acts);
    for (const w of this._overlaps(s)) row.append(this._el("div", "warn", "\u26a0 " + w));
    return row;
  }

  async _identify(s, btn, row) {
    btn.disabled = true;
    btn.textContent = "Flashing\u2026";
    row.querySelector(".serr")?.remove();
    try {
      await this._hass.callWS({ type: "light_show_studio/identify", entry_id: s.entry_id });
    } catch (err) {
      row.append(this._el("div", "serr", (typeof err === "string" ? err : err?.message) || "Couldn't identify this light."));
    }
    setTimeout(() => {
      btn.textContent = "Identify";
      btn.disabled = false;
    }, 3000);
  }

  _openEdit(s, row) {
    this._editing = s.entry_id;
    row.querySelector(".edit")?.remove();
    const f = this._el("form", "edit");
    const field = (cls, label, type, value, attrs = {}) => {
      const l = this._el("label", cls);
      l.append(label);
      const i = document.createElement("input");
      i.type = type;
      i.value = value;
      Object.assign(i, attrs);
      l.append(i);
      f.append(l);
      return i;
    };
    const nm = field("nm", "Name", "text", s.title, { required: true });
    const st = field("", "Start pixel", "number", s.start_pixel, { min: 1, max: this._info.max, step: 1, required: true });
    const bl = field("", "Bulbs", "number", s.bulbs, { min: 1, max: this._info.max, step: 1, required: true });
    const free = this._nextFree(s);
    const hint = this._el("div", "free", "Next free start pixel: ");
    const use = this._el("button", "", String(free));
    use.type = "button";
    use.onclick = () => (st.value = free);
    hint.append(use);
    const err = this._el("div", "serr");
    err.hidden = true;
    const save = this._el("button", "btn sm", "Save");
    save.type = "submit";
    const cancel = this._el("button", "btn alt sm", "Cancel");
    cancel.type = "button";
    cancel.onclick = () => {
      this._editing = null;
      this._renderStrands();
    };
    f.append(hint, err, save, cancel);
    f.onsubmit = async (e) => {
      e.preventDefault();
      const msg = (t) => ((err.textContent = t), (err.hidden = !t));
      const msgs = [];
      const start = Number(st.value), bulbs = Number(bl.value), title = nm.value.trim();
      if (!title) msgs.push("Enter a name.");
      if (!Number.isInteger(start) || start < 1) msgs.push("Start pixel must be 1 or more.");
      if (!Number.isInteger(bulbs) || bulbs < 1) msgs.push("Bulbs must be 1 or more.");
      if (!msgs.length && start + bulbs - 1 > this._info.max) msgs.push(`The last pixel can't go past ${this._info.max}.`);
      if (msgs.length) return msg(msgs[0]);
      const data = { type: "light_show_studio/strand/update", entry_id: s.entry_id };
      if (title !== s.title) data.title = title;
      if (start !== s.start_pixel) data.start_pixel = start;
      if (bulbs !== s.bulbs) data.bulbs = bulbs;
      save.disabled = true;
      msg("");
      try {
        if (Object.keys(data).length > 2) await this._hass.callWS(data);
        this._editing = null;
        await this._loadStrands();
        setTimeout(() => this._loadStrands(), 2000); // the entry reloads in the background
      } catch (e2) {
        msg((typeof e2 === "string" ? e2 : e2?.message) || "Couldn't save.");
        save.disabled = false;
      }
    };
    row.append(f);
    nm.focus();
  }

  _renderXl(sorted) {
    const box = this._xlBox;
    const port = this._info.ddp;
    const total = sorted.reduce((m, s) => Math.max(m, LightShowStudioPanel._range(s)[1]), 0);
    box.hidden = !sorted.length;
    box.textContent = "";
    const line = `DDP \u00b7 ${location.hostname}:${port} \u00b7 ${total} pixels (${total * 3} channels)`;
    const text = [
      "Controller type: Ethernet / DDP",
      `IP address: ${location.hostname}`,
      `Port: ${port}`,
      "Start channel: 1",
      `Total channels: ${total * 3}`,
      "",
      ...sorted.map((s) => {
        const [p0, p1] = LightShowStudioPanel._range(s);
        return `${s.title}: channels ${(p0 - 1) * 3 + 1}\u2013${p1 * 3} (pixels ${p0}\u2013${p1})`;
      }),
    ].join("\n");
    const copy = this._el("button", "btn alt sm", "Copy");
    copy.onclick = () => this._copy(text, copy, "Copy");
    box.append(this._el("h2", "", "For xLights / EZPlayer"), this._el("div", "line", line), copy);
  }

  async _copy(text, btn, restore) {
    let ok = false;
    try {
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch (e) {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.cssText = "position:fixed;opacity:0";
      this._root.append(ta);
      ta.select();
      try {
        ok = document.execCommand("copy");
      } catch (e2) {
        ok = false;
      }
      ta.remove();
    }
    btn.textContent = ok ? "Copied" : "Couldn't copy";
    clearTimeout(btn._t);
    btn._t = setTimeout(() => (btn.textContent = restore), 1500);
  }

  // --- studio tab ----------------------------------------------------------
  _resolveStrand(key) {
    if (!key) return null;
    return (this._strands || []).find((s) => s.entry_id === key || s.entity_id === key) || null;
  }

  _openStudio(s) {
    this._studioSel = s.entry_id;
    storedLight.set(s.entry_id);
    this._formKey = null; // reload the settings form for this light
    this._select("studio", true);
    this.scrollTo?.(0, 0);
  }

  _currentStrand() {
    const list = this._strands || [];
    return this._resolveStrand(this._studioSel) || list[0] || null;
  }

  _buildStudio() {
    const sec = this._sections.studio;
    sec.innerHTML = `
      <div class="box empty st-empty" hidden></div>
      <div class="st-body" hidden>
        <div class="box st-top">
          <label>Light<select class="pick" aria-label="Choose a light"></select></label>
          <span class="sdot" role="img"></span><span class="state"></span>
          <button class="btn alt sm idb" type="button">Identify</button>
        </div>
        <div class="st-live"></div>
        <div class="box st-ctl">
          <div class="st-mode"><span class="ml" role="status"></span>
            <button class="btn sm back" type="button">Hand back to the show</button></div>
          <div class="st-row">
            <button class="pw" type="button" role="switch" aria-checked="false">${svg("M13 3h-2v10h2zm4.8 2.2-1.4 1.4A6 6 0 1 1 7.6 6.6L6.2 5.2a8 8 0 1 0 11.6 0z", 20)}<span>Power</span></button>
            <label class="bri">Brightness<input class="brs" type="range" min="1" max="255" step="1" aria-label="Brightness"><span class="bv"></span></label>
          </div>
          <div class="sws" role="group" aria-label="Colour"></div>
        </div>
        <div class="box st-fx"><h2>Effects</h2><div class="groups"></div></div>
        <form class="box st-set edit" novalidate>
          <h2 style="flex:1 0 100%;margin:0">Settings</h2>
          <label class="nm">Name<input class="f-nm" type="text" required maxlength="100"></label>
          <label class="ip">IP address<input class="f-ip" type="text" inputmode="decimal" autocomplete="off" required></label>
          <label>Start pixel<input class="f-st" type="number" step="1" required></label>
          <label>Bulbs<input class="f-bl" type="number" step="1" required></label>
          <div class="live" aria-live="polite"></div>
          <div class="free">Next free start pixel: <button type="button" class="use"></button></div>
          <div class="warns"></div>
          <div class="serr" role="alert" hidden></div>
          <div class="btnrow"><button class="btn sm save" type="submit">Save</button>
            <button class="btn alt sm reset" type="button">Reset</button>
            <button class="btn alt sm rm" type="button">Remove light</button></div>
          <div class="free note">Changing the IP address reloads the light.</div>
        </form>
        <div class="serr st-err" role="alert" hidden></div>
      </div>`;
    const q = (c) => sec.querySelector(c);
    this._st = {
      empty: q(".st-empty"), body: q(".st-body"), pick: q(".pick"), dot: q(".sdot"), state: q(".state"),
      idb: q(".idb"), live: q(".st-live"), ml: q(".ml"), back: q(".back"), pw: q(".pw"), brs: q(".brs"), bv: q(".bv"),
      sws: q(".sws"), groups: q(".groups"), fx: q(".st-fx"), ctl: q(".st-ctl"), form: q(".st-set"), err: q(".st-err"),
      nm: q(".f-nm"), ip: q(".f-ip"), st: q(".f-st"), bl: q(".f-bl"), liveTxt: q(".st-set .live"),
      use: q(".use"), warns: q(".warns"), ferr: q(".st-set .serr"), save: q(".save"), reset: q(".reset"),
      rm: q(".rm"),
    };
    st.rm.onclick = () => this._studioRemove();
    const st = this._st;
    st.pick.onchange = () => {
      const s = this._resolveStrand(st.pick.value);
      if (s) this._openStudio(s);
    };
    st.idb.onclick = () => {
      const s = this._currentStrand();
      if (s) this._studioIdentify(s);
    };
    st.back.onclick = () => this._svc("turn_on", { effect: SHOW });
    st.pw.onclick = () => this._svc(this._entityState()?.state === "on" ? "turn_off" : "turn_on", {});
    st.brs.oninput = () => {
      st.bv.textContent = Math.round((st.brs.value / 255) * 100) + "%";
      clearTimeout(this._briT);
      this._briT = setTimeout(() => this._svc("turn_on", { brightness: Number(st.brs.value) }), 150);
    };
    st.brs.onchange = () => {
      clearTimeout(this._briT);
      this._svc("turn_on", { brightness: Number(st.brs.value) });
    };
    for (const c of SWATCHES) {
      const b = this._el("button", "sw");
      b.type = "button";
      b.style.background = c;
      b.title = c;
      b.dataset.c = c;
      b.setAttribute("aria-label", `Colour ${c}`);
      b.onclick = () => this._setColour(c);
      st.sws.append(b);
    }
    this._picker = Object.assign(document.createElement("input"), { type: "color", value: "#ff8c00", title: "Any colour" });
    this._picker.setAttribute("aria-label", "Custom colour");
    this._picker.onchange = () => this._setColour(this._picker.value);
    st.sws.append(this._picker);
    const onInput = () => {
      this._formDirty = true;
      this._renderFormLive();
    };
    for (const i of [st.nm, st.ip, st.st, st.bl]) i.oninput = onInput;
    st.use.onclick = () => {
      st.st.value = st.use.textContent;
      onInput();
    };
    st.reset.onclick = () => {
      this._formKey = null;
      this._renderStudio();
    };
    st.form.onsubmit = (e) => {
      e.preventDefault();
      this._studioSave();
    };
  }

  _entityState() {
    const s = this._currentStrand();
    return s?.entity_id ? this._hass?.states[s.entity_id] || null : null;
  }

  _studioError(text) {
    this._st.err.textContent = text || "";
    this._st.err.hidden = !text;
  }

  async _svc(service, data) {
    const s = this._currentStrand();
    if (!s?.entity_id || !this._hass) return;
    try {
      await this._hass.callService("light", service, { entity_id: s.entity_id, ...data });
      this._studioError("");
    } catch (err) {
      this._studioError((typeof err === "string" ? err : err?.message) || "That didn't work.");
    }
  }

  _setColour(hex) {
    const n = parseInt(hex.slice(1), 16);
    this._svc("turn_on", { rgb_color: [(n >> 16) & 255, (n >> 8) & 255, n & 255] });
  }

  async _studioIdentify(s) {
    const b = this._st.idb;
    b.disabled = true;
    b.textContent = "Flashing…";
    try {
      await this._hass.callWS({ type: "light_show_studio/identify", entry_id: s.entry_id });
      this._studioError("");
    } catch (err) {
      this._studioError((typeof err === "string" ? err : err?.message) || "Couldn't identify this light.");
    }
    setTimeout(() => this._renderStudio(), 3000);
  }

  static _kind(s) {
    return `${{ twinkly: "Twinkly", wled: "WLED" }[s.kind] || "Govee"}${s.model ? " " + s.model : ""}`;
  }

  // header, live card, form: what only changes with the strands list or the selection
  _renderStudio() {
    const st = this._st;
    if (!st) return;
    const list = this._strands;
    const none = !!list && !list.length;
    st.empty.hidden = !(none || (!list && this._strandsError));
    st.body.hidden = !list || none;
    if (!st.empty.hidden) {
      st.empty.textContent = none ? "No lights yet." : this._strandsError;
      if (none) {
        st.empty.append(document.createElement("br"));
        const b = Object.assign(document.createElement("button"), { className: "btn", textContent: "Add a light" });
        b.onclick = () => this._navigate(INTEGRATION_PATH);
        st.empty.append(b);
      }
    }
    if (st.body.hidden) return;
    const s = this._currentStrand();
    this._studioSel = s.entry_id;
    const online = (x) => (x.online === true ? "●" : x.online === false ? "○" : "◌");
    const sig = JSON.stringify(list.map((x) => [x.entry_id, x.title, x.kind, x.model, x.host, x.online]));
    if (sig !== this._pickSig) {
      this._pickSig = sig;
      st.pick.textContent = "";
      for (const x of list) {
        const o = Object.assign(document.createElement("option"), { value: x.entry_id });
        o.textContent = `${online(x)} ${x.title} — ${LightShowStudioPanel._kind(x)} · ${x.host}`;
        st.pick.append(o);
      }
    }
    st.pick.value = s.entry_id;
    const state = !s.loaded ? ["", "Not loaded"] : s.online === true ? ["on", "Online"] : s.online === false ? ["off", "Offline"] : ["", "checking…"];
    st.dot.className = "sdot " + state[0];
    st.dot.setAttribute("aria-label", state[1]);
    st.state.className = "state " + state[0];
    st.state.textContent = state[1];
    st.idb.disabled = !s.loaded || s.online === false;
    st.idb.textContent = "Identify";
    // live card for this light only (one subscription at a time)
    if (s.entity_id) {
      if (!this._studioCards.has(s.entity_id))
        this._studioCards.set(s.entity_id, this._card("light-show-studio-card", { entity: s.entity_id, size: 1.7 }));
      const card = this._studioCards.get(s.entity_id);
      if (st.live.firstChild !== card) st.live.replaceChildren(card);
      this._feed(card);
    } else {
      st.live.replaceChildren(Object.assign(this._el("div", "box empty", "This light isn't loaded, so there's nothing to show yet.")));
    }
    // settings form
    st.form.hidden = !this._isAdmin();
    const key = s.entry_id;
    if (this._formKey !== key || !this._formDirty) {
      if (this._formKey !== key || !st.form.contains(this._root.getRootNode().activeElement)) {
        this._formKey = key;
        this._formDirty = false;
        st.nm.value = s.title;
        st.ip.value = s.host || "";
        st.st.value = s.start_pixel;
        st.bl.value = s.bulbs;
        st.ferr.hidden = true;
        st.save.disabled = false;
      }
    }
    this._renderFormLive();
    this._renderStudioControls();
  }

  _renderFormLive() {
    const st = this._st;
    const s = this._currentStrand();
    if (!s) return;
    const start = Number(st.st.value), bulbs = Number(st.bl.value);
    const ok = Number.isInteger(start) && Number.isInteger(bulbs) && start >= 1 && bulbs >= 1;
    st.liveTxt.textContent = ok
      ? `Pixels ${start}–${start + bulbs - 1} · xLights channels ${(start - 1) * 3 + 1}–${(start + bulbs - 1) * 3}`
      : "Enter a start pixel and bulb count.";
    st.use.textContent = String(this._nextFree(s));
    st.warns.textContent = "";
    if (ok) for (const w of this._overlaps({ ...s, start_pixel: start, bulbs })) st.warns.append(this._el("div", "warn", "⚠ " + w));
  }

  async _studioSave() {
    const st = this._st;
    const s = this._currentStrand();
    if (!s) return;
    const max = this._info?.max || 2040;
    const title = st.nm.value.trim(), host = st.ip.value.trim();
    const start = Number(st.st.value), bulbs = Number(st.bl.value);
    const fail = (t) => ((st.ferr.textContent = t), (st.ferr.hidden = !t));
    let msg = "";
    if (!title) msg = "Enter a name.";
    else if (!IPV4.test(host)) msg = "Enter the IP address like 192.168.4.42.";
    else if (!Number.isInteger(start) || start < 1) msg = "Start pixel must be 1 or more.";
    else if (!Number.isInteger(bulbs) || bulbs < 1) msg = "Bulbs must be 1 or more.";
    else if (start + bulbs - 1 > max) msg = `The last pixel can't go past ${max}.`;
    if (msg) return fail(msg);
    const data = { type: "light_show_studio/strand/update", entry_id: s.entry_id };
    if (title !== s.title) data.title = title;
    if (host !== s.host) data.host = host;
    if (start !== s.start_pixel) data.start_pixel = start;
    if (bulbs !== s.bulbs) data.bulbs = bulbs;
    if (Object.keys(data).length <= 2) return fail("Nothing changed.");
    st.save.disabled = true;
    fail("");
    try {
      await this._hass.callWS(data);
      this._formDirty = false;
      this._formKey = null;
      await this._loadStrands();
      setTimeout(() => this._loadStrands(), 2000); // the entry reloads in the background
    } catch (err) {
      fail((typeof err === "string" ? err : err?.message) || "Couldn't save.");
      st.save.disabled = false;
    }
  }

  async _studioRemove() {
    const st = this._st;
    const s = this._currentStrand();
    if (!s) return;
    const what = `"${s.title}"${s.host ? ` (${s.host})` : ""}`;
    if (!window.confirm(`Remove ${what} from Light Show Studio? Its device and lights are deleted. You can add it again later.`)) return;
    st.rm.disabled = true;
    try {
      await this._hass.callWS({ type: "light_show_studio/strand/remove", entry_id: s.entry_id });
      this._formDirty = false;
      this._formKey = null;
      await this._loadStrands();
      const next = (this._strands || [])[0];
      if (next) this._openStudio(next);
    } catch (err) {
      st.ferr.textContent = (typeof err === "string" ? err : err?.message) || "Couldn't remove the light.";
      st.ferr.hidden = false;
    } finally {
      st.rm.disabled = false;
    }
  }

  // everything that follows the light's state
  _renderStudioControls() {
    const st = this._st;
    const s = this._currentStrand();
    if (!st || !s || !this._hass) return;
    const e = s.entity_id ? this._hass.states[s.entity_id] : null;
    const usable = !!e && e.state !== "unavailable" && e.state !== "unknown";
    const on = usable && e.state === "on";
    const a = (usable && e.attributes) || {};
    st.ctl.classList.toggle("ctl-off", !usable);
    st.fx.classList.toggle("ctl-off", !usable);
    st.pw.setAttribute("aria-checked", String(on));
    const effect = on ? a.effect : null;
    const following = on && effect === SHOW;
    let line = "Offline";
    if (!s.entity_id) line = "Not loaded";
    else if (usable && !on) line = "Off";
    else if (following) line = "Following the show";
    else if (on) line = effect ? `Showing ${effect}` : a.per_bulb ? "Showing your painting" : "Showing a colour";
    st.ml.textContent = line;
    st.back.hidden = !on || following;
    if (document.activeElement !== st.brs && this._root.getRootNode().activeElement !== st.brs && a.brightness != null) {
      st.brs.value = a.brightness;
    }
    st.bv.textContent = Math.round(((a.brightness ?? Number(st.brs.value)) / 255) * 100) + "%";
    const rgb = on && !effect && Array.isArray(a.rgb_color) ? a.rgb_color : null;
    const hex = rgb ? "#" + rgb.map((v) => v.toString(16).padStart(2, "0")).join("") : "";
    st.sws.querySelectorAll(".sw").forEach((b) => b.classList.toggle("sel", !!hex && b.dataset.c === hex));
    if (hex && this._root.getRootNode().activeElement !== this._picker) this._picker.value = hex;
    // effect chips
    const list = Array.isArray(a.effect_list) ? a.effect_list : [];
    const sig = list.join("|");
    if (sig !== this._fxSig && (usable || !this._fxSig)) {
      this._fxSig = sig;
      st.groups.textContent = "";
      const group = (title, names, first) => {
        if (!names.length) return;
        const g = this._el("div", "grp");
        g.append(this._el("h3", "", title));
        const row = this._el("div", "chips");
        for (const n of names) {
          const b = this._el("button", "chip" + (n === SHOW ? " show" : ""), n === SHOW ? "Follow the show" : n);
          b.type = "button";
          b.dataset.fx = n;
          b.onclick = () => this._svc("turn_on", { effect: n });
          row.append(b);
        }
        g.append(row);
        st.groups.append(g);
      };
      group("Show", list.filter((n) => n === SHOW));
      group("Seasonal", list.filter((n) => n !== SHOW && SEASONAL.test(n)));
      group("More effects", list.filter((n) => n !== SHOW && !SEASONAL.test(n)));
      if (!list.length) st.groups.append(this._el("div", "muted", "Effects show up when the light is online."));
    }
    st.groups.querySelectorAll(".chip").forEach((b) => b.setAttribute("aria-pressed", String(on && effect === b.dataset.fx)));
    if (s.entity_id) this._feed(this._studioCards.get(s.entity_id));
  }

  // --- live updates ------------------------------------------------------
  _update() {
    const lights = this._lights();
    const player = this._findPlayer();
    const shows = (player?.attributes.queue || []).length;
    const status = { lights: lights.length > 0, player: !!player, shows: shows > 0 };
    if (this._tab === null) {
      const saved = stored.get();
      const ok = Object.values(status).every(Boolean);
      this._select(TABS.some(([id]) => id === saved) ? saved : ok ? "play" : "setup", false);
    }
    this._dot.hidden = Object.values(status).every(Boolean);
    this._syncLights(lights);
    if (this._tab === "studio") this._renderStudioControls();
    this._feed(this._player);
    this._feed(this._adder);
    this._renderSetup(status, shows);
  }

  _findPlayer() {
    const id = Object.keys(this._hass.states).find(
      (k) => k.startsWith("media_player.") && this._hass.states[k].attributes.queue !== undefined
    );
    this._player_entity = id;
    return id ? this._hass.states[id] : null;
  }

  _syncLights(ids) {
    const key = ids.join();
    if (key !== this._lightKey) {
      this._lightKey = key;
      for (const [id, el] of this._cards) if (!ids.includes(id)) (el.remove(), this._cards.delete(id));
      for (const id of ids) {
        if (!this._cards.has(id)) this._cards.set(id, this._card("light-show-studio-card", { entity: id, open_studio: true }));
        this._lightsGrid.append(this._cards.get(id)); // keeps entity order
      }
      this._emptyLights?.remove();
      this._emptyLights = null;
      if (!ids.length) {
        this._emptyLights = Object.assign(document.createElement("div"), { className: "box empty" });
        this._emptyLights.innerHTML = `No lights yet.<br>`;
        const b = Object.assign(document.createElement("button"), { className: "btn", textContent: "Add a light" });
        b.onclick = () => this._navigate(INTEGRATION_PATH);
        this._emptyLights.append(b);
        this._sections.lights.prepend(this._emptyLights);
      }
    }
    this._cards.forEach((el) => this._feed(el));
  }

  // --- setup tab ---------------------------------------------------------
  _buildSetup() {
    this._sections.setup.innerHTML = `
      <div class="box">
        <h2>Setup checklist</h2>
        <ul class="check"></ul>
      </div>
      <div class="box muted">Live from xLights / EZPlayer: send DDP to <code class="host"></code></div>`;
    this._sections.setup.querySelector(".host").textContent = `${location.hostname}:4048`;
    this._checks = this._sections.setup.querySelector(".check");
  }

  _renderSetup(status, shows) {
    const rows = [
      ["lights", "Lights added", status.lights ? "" : "Add a light", status.lights ? "Add another light" : "Add a light",
        () => this._navigate(INTEGRATION_PATH)],
      ["player", "Show player set up", status.player ? "" : "Add the Light Show Player", status.player ? "Open integration" : "Set up the player",
        () => this._navigate(INTEGRATION_PATH)],
      ["shows", "Shows added", status.shows ? `${shows} show${shows === 1 ? "" : "s"}` : "No shows yet", status.shows ? "Add more shows" : "Add shows",
        () => this._select("add", true)],
    ];
    const sig = JSON.stringify([status, shows]);
    if (sig === this._setupSig) return;
    this._setupSig = sig;
    this._checks.innerHTML = "";
    for (const [id, label, sub, btn, go] of rows) {
      const li = document.createElement("li");
      const ok = status[id];
      li.innerHTML = `<span class="mark ${ok ? "ok" : ""}" role="img" aria-label="${ok ? "Done" : "Not done"}">${ok ? "✓" : "✗"}</span>
        <span class="t"></span>`;
      const t = li.querySelector(".t");
      t.textContent = label;
      if (sub) t.append(Object.assign(document.createElement("small"), { textContent: sub }));
      const b = Object.assign(document.createElement("button"), { className: "btn" + (ok ? " alt" : ""), textContent: btn });
      b.onclick = go;
      li.append(b);
      this._checks.append(li);
    }
  }

  _navigate(path) {
    history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
  }
}

customElements.define("light-show-studio-panel", LightShowStudioPanel);
