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
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._update();
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
    return Object.keys(this._hass.states).filter((id) => {
      const a = this._hass.states[id].attributes;
      return id.startsWith("light.") && a.bulbs && (a.effect_list || []).includes("Light show");
    });
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
    this._sections.lights.innerHTML = `<div class="grid"></div>`;
    this._lightsGrid = this._sections.lights.firstChild;
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
