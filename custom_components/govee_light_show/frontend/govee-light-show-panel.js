// Govee Light Show sidebar app: Play / Lights / Add shows / Setup in one page. Ships with govee_light_show.
// Reuses the two cards, so govee-light-show-card.js and govee-light-show-player-card.js must be loaded too.
// Custom element: govee-light-show-panel (HA sets hass, narrow, route, panel on it).

const TABS = [
  ["play", "Play", "M8 5v14l11-7z"],
  ["lights", "Lights", "M12 2a7 7 0 0 0-4 12.7V17a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-2.3A7 7 0 0 0 12 2zM9 20h6v1a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1z"],
  ["add", "Add shows", "M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6z"],
  ["setup", "Setup", "M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z"],
];
const INTEGRATION_PATH = "/config/integrations/integration/govee_light_show";
const STORE_KEY = "govee_light_show_panel_tab";

const svg = (d, size = 24) =>
  `<svg viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true"><path fill="currentColor" d="${d}"/></svg>`;

const stored = {
  get() {
    try {
      return localStorage.getItem(STORE_KEY);
    } catch (e) {
      return null;
    }
  },
  set(v) {
    try {
      localStorage.setItem(STORE_KEY, v);
    } catch (e) {
      /* storage blocked: tab just isn't remembered */
    }
  },
};

class GoveeLightShowPanel extends HTMLElement {
  constructor() {
    super();
    this._tab = null;
    this._cards = new Map(); // entity id -> light card
    this._strands = null; // from govee_light_show/strands
    this._editing = null; // entry id whose inline editor is open
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
        /* phones: tabs become a bottom bar */
        .narrow nav { position: fixed; left: 0; right: 0; bottom: 0; z-index: 5; background: var(--card-background-color, #fff);
                      color: var(--secondary-text-color); border-top: 1px solid var(--divider-color);
                      padding-bottom: env(safe-area-inset-bottom); }
        .narrow nav button { flex: 1; flex-direction: column; justify-content: center; gap: 2px; height: 60px; padding: 0 2px;
                             font-size: 11px; text-transform: none; letter-spacing: 0; opacity: 1; min-width: 0; }
        .narrow nav button[aria-selected=true] { color: var(--primary-color); box-shadow: inset 0 3px 0 var(--primary-color); }
        .narrow nav .dot { position: absolute; top: 8px; left: calc(50% + 8px); }
        .narrow main { padding: 12px 12px calc(76px + env(safe-area-inset-bottom)); }
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

    this._player = this._card("govee-light-show-player-card", { mode: "player" });
    this._sections.play.append(this._player);
    this._adder = this._card("govee-light-show-player-card", { mode: "upload" });
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
    if (id === "lights") this._loadStrands();
  }

  // --- strands list --------------------------------------------------------
  _startPolling() {
    if (this._poll || !this._hass || !this.isConnected) return;
    this._loadStrands();
    this._poll = setInterval(() => {
      if (this._tab === "lights" && !document.hidden && !this._editing) this._loadStrands();
    }, 15000);
  }

  async _loadStrands() {
    if (this._loading || !this._hass) return;
    this._loading = true;
    try {
      const r = await this._hass.callWS({ type: "govee_light_show/strands" });
      this._strands = Array.isArray(r?.strands) ? r.strands : [];
      this._info = { ddp: r?.ddp_port || 4048, max: r?.max_pixels || 2040 };
      this._strandsError = "";
    } catch (err) {
      this._strandsError = (typeof err === "string" ? err : err?.message) || "Couldn't load the strands.";
    }
    this._loading = false;
    this._renderStrands();
    if (this._hass) this._update();
  }

  _isAdmin() {
    return !!this._hass?.user?.is_admin;
  }

  static _range(s) {
    return [s.start_pixel, s.start_pixel + s.bulbs - 1];
  }

  _overlaps(s) {
    const [a0, a1] = GoveeLightShowPanel._range(s);
    const out = [];
    for (const o of this._strands) {
      if (o === s) continue;
      const [b0, b1] = GoveeLightShowPanel._range(o);
      const lo = Math.max(a0, b0), hi = Math.min(a1, b1);
      if (lo <= hi) out.push(`Overlaps ${o.title}: pixels ${lo}\u2013${hi}`);
    }
    return out;
  }

  _nextFree(except) {
    let end = 0;
    for (const o of this._strands) if (o !== except) end = Math.max(end, GoveeLightShowPanel._range(o)[1]);
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
    const [p0, p1] = GoveeLightShowPanel._range(s);
    const state = !s.loaded ? ["", "Not loaded"] : s.online === true ? ["on", "Online"]
      : s.online === false ? ["off", "Offline" + (s.last_seen ? ` \u00b7 last seen ${GoveeLightShowPanel._ago(s.last_seen)}` : " \u00b7 never seen")]
      : ["", "checking\u2026"];
    const info = this._el("div", "info");
    const name = this._el("div", "name");
    const dot = this._el("span", "sdot " + state[0]);
    dot.setAttribute("role", "img");
    dot.setAttribute("aria-label", state[1]);
    name.append(dot, this._el("span", "n", s.title), this._el("span", "state " + state[0], state[1]));
    const meta = this._el("div", "meta");
    const kind = `${s.kind === "twinkly" ? "Twinkly" : "Govee"}${s.model ? " " + s.model : ""}`;
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
      await this._hass.callWS({ type: "govee_light_show/identify", entry_id: s.entry_id });
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
      const data = { type: "govee_light_show/strand/update", entry_id: s.entry_id };
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
    const total = sorted.reduce((m, s) => Math.max(m, GoveeLightShowPanel._range(s)[1]), 0);
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
        const [p0, p1] = GoveeLightShowPanel._range(s);
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
        if (!this._cards.has(id)) this._cards.set(id, this._card("govee-light-show-card", { entity: id }));
        this._lightsGrid.append(this._cards.get(id)); // keeps entity order
      }
      this._emptyLights?.remove();
      this._emptyLights = null;
      if (!ids.length) {
        this._emptyLights = Object.assign(document.createElement("div"), { className: "box empty" });
        this._emptyLights.innerHTML = `No Govee string lights yet.<br>`;
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
      ["lights", "Lights added", status.lights ? "" : "Add a Govee string light", status.lights ? "Add another light" : "Add a light",
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

customElements.define("govee-light-show-panel", GoveeLightShowPanel);
