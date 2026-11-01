// Light Show Studio sidebar app: Play / Library / Schedule / Lights / Studio in one page. Ships with light_show_studio.
// Reuses the two cards, so light-show-studio-card.js and light-show-studio-player-card.js must be loaded too.
// Custom element: light-show-studio-panel (HA sets hass, narrow, route, panel on it).

const TABS = [
  ["play", "Play", "M8 5v14l11-7z"],
  ["library", "Library", "M4 6H2v14c0 1.1.9 2 2 2h14v-2H4zm16-4H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-1 9H9V9h10zm-4 4H9v-2h6zm4-8H9V5h10z"],
  ["schedule", "Schedule", "M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z"],
  ["lights", "Lights", "M12 2a7 7 0 0 0-4 12.7V17a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-2.3A7 7 0 0 0 12 2zM9 20h6v1a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1z"],
  ["studio", "Studio", "M3 17v2h6v-2H3zM3 5v2h10V5H3zm10 16v-2h8v-2h-8v-2h-2v6h2zM7 9v2H3v2h4v2h2V9H7zm14 4v-2H11v2h10zm-6-4h2V7h4V5h-4V3h-2v6z"],
];
// tabs from older versions of the app that now live elsewhere
const OLD_TABS = { add: "library", setup: null };
const INTEGRATION_PATH = "/config/integrations/integration/light_show_studio";
const STORE_KEY = "light_show_studio_panel_tab";
const SEL_KEY = "light_show_studio_panel_light";
const BANNER_KEY = "light_show_studio_panel_banner"; // the first-run banner was dismissed for this set of missing steps
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
const storedBanner = store(BANNER_KEY);
const storedLight = store(SEL_KEY);

class LightShowStudioPanel extends HTMLElement {
  constructor() {
    super();
    this._tab = null;
    this._strands = null; // from light_show_studio/strands
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
    if (this._tab === "schedule") this._subscribeSchedule();
  }

  disconnectedCallback() {
    clearInterval(this._poll);
    this._poll = null;
    this._unsubscribeSchedule();
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
        main { flex: 1; overflow: auto; padding: 16px; width: 100%; max-width: 900px; margin: 0 auto; }
        section > * + * { margin-top: 16px; }
        .box { background: var(--card-background-color, #fff); border-radius: var(--ha-card-border-radius, 12px);
               border: 1px solid var(--divider-color); padding: 16px; line-height: 1.5; }
        .box h2 { font-size: 16px; font-weight: 500; margin: 0 0 8px; }
        .box ol { margin: 0; padding-left: 22px; }
        .lhead, .banner .bh { display: flex; align-items: center; justify-content: space-between; gap: 8px 12px; }
        .lhead h2 { margin: 0; font-size: 18px; font-weight: 500; }
        .banner { border-color: var(--primary-color); }
        .banner ul { list-style: none; margin: 8px 0 0; padding: 0; }
        .banner li { display: flex; align-items: center; justify-content: space-between; gap: 8px 12px; flex-wrap: wrap; padding: 6px 0; }
        .banner .x { width: 40px; height: 40px; border-radius: 50%; flex: none; display: inline-flex; align-items: center; justify-content: center; }
        .banner .x:hover { background: var(--secondary-background-color, rgba(127,127,127,.15)); }
        details.add > summary { cursor: pointer; font-size: 16px; font-weight: 500; min-height: 32px; display: flex; align-items: center; }
        details.add[open] > summary { margin-bottom: 8px; }
        details.add .ulcard > * { display: block; margin: 12px 0 0; }
        .libcard > * { display: block; margin: 0; }
        .box li + li { margin-top: 6px; }
        .muted { color: var(--secondary-text-color); font-size: 13px; }
        .btn { font-size: 14px; font-weight: 500; padding: 8px 16px; min-height: 40px; border-radius: 20px;
               background: var(--primary-color); color: var(--text-primary-color, #fff); }
        .btn.alt { background: none; color: var(--primary-color); border: 1px solid var(--divider-color); }
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
        .narrow main { padding: 12px 12px calc(76px + env(safe-area-inset-bottom)); }
        .btn.rm { color: var(--error-color, #db4437); border-color: var(--error-color, #db4437); margin-left: auto; }
        /* playlists + schedule */
        .pl { border: 1px solid var(--divider-color); border-radius: 10px; margin-top: 10px; overflow: hidden; }
        .plh { display: flex; align-items: center; gap: 8px; width: 100%; min-height: 48px; padding: 6px 12px; text-align: left; }
        .plh:hover { background: var(--secondary-background-color, rgba(127,127,127,.12)); }
        .plh .pn { flex: 1; min-width: 0; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .plh .chev { flex: none; transition: transform .12s; color: var(--secondary-text-color); }
        .plh[aria-expanded=true] .chev { transform: rotate(180deg); }
        .ple { padding: 4px 12px 12px; border-top: 1px solid var(--divider-color); }
        .ple h3, .sed h3 { font-size: 13px; font-weight: 500; margin: 14px 0 6px; color: var(--secondary-text-color); }
        .row2 { display: flex; align-items: center; gap: 6px; min-height: 44px; border-bottom: 1px solid var(--divider-color); }
        .row2:last-child { border-bottom: none; }
        .row2 .rn { flex: 1; min-width: 0; overflow-wrap: anywhere; }
        .row2 .rn.gone { color: var(--secondary-text-color); font-style: italic; }
        .ib2 { width: 40px; height: 40px; border-radius: 50%; flex: none; display: inline-flex; align-items: center; justify-content: center; }
        .ib2:hover:not(:disabled) { background: var(--secondary-background-color, rgba(127,127,127,.15)); }
        .ib2:disabled { opacity: .3; cursor: default; }
        .inl { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
        .inl input[type=text] { flex: 1 1 160px; min-width: 0; }
        input[type=text], input[type=time], input[type=number], textarea { font: inherit; font-size: 14px; padding: 6px 8px; min-height: 40px;
                 border: 1px solid var(--divider-color); border-radius: 8px; background: var(--card-background-color, #fff);
                 color: var(--primary-text-color); max-width: 100%; min-width: 0; }
        .btn.danger { color: var(--error-color, #db4437); border-color: var(--error-color, #db4437); }
        .btn.warnb { background: var(--error-color, #db4437); }
        .sbanner { display: flex; flex-direction: column; gap: 4px; }
        .sbanner .l1 { font-weight: 500; overflow-wrap: anywhere; }
        .sbanner .l2 { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; font-size: 14px; }
        .sbanner .stt { min-width: 0; overflow-wrap: anywhere; }
        .sbanner .sdot { display: inline-block; margin-right: 2px; }
        .sdot.run { background: var(--success-color, #43a047); }
        .sdot.pause { background: var(--warning-color, #f4a100); }
        .sc { padding: 12px 14px; }
        .sc + .sc { margin-top: 12px; }
        .sc.off .sbody { opacity: .55; }
        .sc .top { display: flex; align-items: center; gap: 10px; }
        .sc .top .sn { flex: 1; min-width: 0; font-weight: 500; font-size: 16px; overflow-wrap: anywhere; }
        .tgl { position: relative; flex: none; width: 52px; height: 32px; border-radius: 16px; background: var(--disabled-text-color, #9e9e9e); }
        .tgl::after { content: ""; position: absolute; top: 4px; left: 4px; width: 24px; height: 24px; border-radius: 50%; background: #fff; transition: transform .12s; }
        .tgl[aria-checked=true] { background: var(--primary-color); }
        .tgl[aria-checked=true]::after { transform: translateX(20px); }
        .tgl:disabled { opacity: .5; cursor: default; }
        .sbody { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; font-size: 14px; }
        .dd { display: flex; gap: 4px; flex-wrap: wrap; }
        .dd span { width: 26px; height: 26px; border-radius: 50%; font-size: 12px; display: inline-flex; align-items: center; justify-content: center;
                   border: 1px solid var(--divider-color); color: var(--secondary-text-color); }
        .dd span.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
        .tag { display: inline-block; font-size: 12px; padding: 2px 8px; border-radius: 10px; background: var(--secondary-background-color, rgba(127,127,127,.15)); }
        .tag.run { background: var(--success-color, #43a047); color: #fff; }
        .tag.pause { background: var(--warning-color, #f4a100); color: #000; }
        .sacts { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
        .sed { border-color: var(--primary-color); }
        .sed form > * + * { margin-top: 14px; }
        .fld { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--secondary-text-color); min-width: 0; }
        .fld > span.lb, .sed h3.lb { font-size: 13px; font-weight: 500; color: var(--secondary-text-color); margin: 0; }
        .fld input[type=text], .fld select { width: 100%; }
        .seg { display: flex; border: 1px solid var(--divider-color); border-radius: 20px; overflow: hidden; }
        .segb { flex: 1 1 0; min-height: 40px; padding: 4px 6px; font-size: 14px; border-right: 1px solid var(--divider-color); min-width: 0; }
        .segb:last-child { border-right: none; }
        .segb[aria-checked=true] { background: var(--primary-color); color: var(--text-primary-color, #fff); }
        .dsel { display: flex; gap: 6px; flex-wrap: wrap; }
        .dsel button { width: 40px; height: 40px; border-radius: 50%; border: 1px solid var(--divider-color); font-size: 14px; }
        .dsel button[aria-pressed=true] { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
        .edge-in { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 8px; }
        .edge-in .note { font-size: 12px; color: var(--secondary-text-color); }
        .chk { display: flex; align-items: center; gap: 10px; min-height: 40px; font-size: 14px; color: var(--primary-text-color); cursor: pointer; }
        .chk input { width: 20px; height: 20px; flex: none; accent-color: var(--primary-color); margin: 0; }
        .seas { display: flex; flex-wrap: wrap; gap: 8px 16px; margin-top: 8px; }
        .seas .md { display: flex; align-items: center; gap: 6px; }
        .seas select { min-width: 76px; }
        .spk-l { max-height: 220px; overflow: auto; border: 1px solid var(--divider-color); border-radius: 8px; padding: 2px 10px; margin-top: 6px; }
        .vrow { display: flex; align-items: center; gap: 10px; margin-top: 4px; }
        .vrow input[type=range] { flex: 1; min-width: 0; height: 40px; accent-color: var(--primary-color); }
        .vrow .bv { width: 3.2em; text-align: right; font-size: 14px; color: var(--primary-text-color); }
        .sed .btnrow { display: flex; gap: 8px; flex-wrap: wrap; }
        .notice { font-size: 13px; color: var(--primary-text-color); }
        details.spk > summary { cursor: pointer; min-height: 40px; display: flex; align-items: center; font-size: 14px; color: var(--primary-text-color); }
      </style>
      <div class="root">
        <header>
          <button class="menu" aria-label="Open the sidebar menu" hidden>${svg("M3 6h18v2H3zm0 5h18v2H3zm0 5h18v2H3z")}</button>
          <h1>Light Shows</h1>
          <nav role="tablist" aria-label="Light Shows sections"></nav>
        </header>
        <main>
          <div class="box banner" role="region" aria-label="Getting started" hidden></div>
          <section id="p-play" role="tabpanel"></section>
          <section id="p-library" role="tabpanel">
            <div class="libcard"></div>
            <details class="box add">
              <summary>Add shows</summary>
              <ol>
                <li>In xLights open your sequence and render it (it saves a <code>.fseq</code> next to the <code>.xsq</code>).</li>
                <li>Add the <code>.fseq</code> and the song here — same name, like <code>My Show.fseq</code> and <code>My Show.m4a</code>.</li>
                <li>Press Play.</li>
              </ol>
              <div class="ulcard"></div>
            </details>
            <div class="box pls" aria-label="Playlists"></div>
          </section>
          <section id="p-schedule" role="tabpanel">
            <div class="box sbanner" role="status" aria-live="polite"></div>
            <div class="lhead"><h2>Schedules</h2><button class="btn sm add-sched" type="button" hidden>Add schedule</button></div>
            <div class="box sed" hidden></div>
            <div class="slist"></div>
          </section>
          <section id="p-lights" role="tabpanel"></section>
          <section id="p-studio" role="tabpanel" class="st"></section>
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
    this._banner = root.querySelector(".banner");
    this._addShows = root.querySelector("details.add");
    this._player = this._card("light-show-studio-player-card", { mode: "player" });
    this._sections.play.append(this._player);
    this._library = this._card("light-show-studio-player-card", { mode: "library", title: "Shows" });
    root.querySelector(".libcard").append(this._library);
    this._adder = this._card("light-show-studio-player-card", { mode: "upload", title: "" });
    root.querySelector(".ulcard").append(this._adder);
    this._sections.lights.innerHTML = `
      <div class="lhead"><h2>Lights</h2><button class="btn alt sm add-light" type="button" hidden>Add a light</button></div>
      <div class="box strands" role="list" aria-label="Light strands" hidden></div>
      <div class="box empty no-lights" hidden>No lights yet.<br><button class="btn" type="button">Add a light</button></div>
      <div class="box xl" hidden></div>`;
    const q = (c) => this._sections.lights.querySelector(c);
    this._strandBox = q(".strands");
    this._xlBox = q(".xl");
    this._addLight = q(".add-light");
    this._noLights = q(".no-lights");
    this._addLight.onclick = q(".no-lights .btn").onclick = () => this._navigate(INTEGRATION_PATH);
    this._buildStudio();
    this._plBox = root.querySelector(".pls");
    this._schBanner = root.querySelector(".sbanner");
    this._sed = root.querySelector(".sed");
    this._schList = root.querySelector(".slist");
    this._addSched = root.querySelector(".add-sched");
    this._addSched.onclick = () => this._openEditor();
    this._renderPlaylists();
    this._renderSchedBanner();
    this._renderSchedules();
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
    if (id === "library") this._loadPlaylists();
    if (id === "schedule") {
      this._loadSchedules();
      this._subscribeSchedule();
    }
  }

  // --- strands list --------------------------------------------------------
  _startPolling() {
    if (this._poll || !this._hass || !this.isConnected) return;
    this._loadStrands();
    this._poll = setInterval(() => {
      if (document.hidden) return;
      if (this._tab === "lights" || this._tab === "studio") this._loadStrands();
      if (this._tab === "library") this._loadPlaylists();
      if (this._tab === "schedule") {
        if (!this._draft) this._loadSchedules();
        this._subscribeSchedule();
      }
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
    const none = !!list && !list.length && !this._strandsError;
    this._noLights.hidden = !none;
    this._addLight.hidden = !list || none;
    if (!list || none) {
      box.hidden = true;
      this._xlBox.hidden = true;
      return;
    }
    box.hidden = false;
    box.textContent = "";
    if (this._strandsError) box.append(Object.assign(document.createElement("div"), { className: "serr", textContent: this._strandsError }));
    const sorted = [...list].sort((a, b) => a.start_pixel - b.start_pixel);
    for (const s of sorted) box.append(this._strandRow(s));
    this._renderXl(sorted);
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
    const kind = LightShowStudioPanel._kind(s);
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
    const st = this._st;
    st.rm.onclick = () => this._studioRemove();
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

  // --- shared bits for playlists and schedules ----------------------------------
  _btn(text, cls, onclick) {
    const b = this._el("button", cls, text);
    b.type = "button";
    if (onclick) b.onclick = onclick;
    return b;
  }

  _err(err, fallback) {
    return (typeof err === "string" ? err : err?.message) || fallback;
  }

  _seg(options, value, label, onPick) {
    const g = this._el("div", "seg");
    g.setAttribute("role", "radiogroup");
    g.setAttribute("aria-label", label);
    for (const [v, t] of options) {
      const b = this._btn(t, "segb", () => onPick(v));
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", String(v === value));
      g.append(b);
    }
    return g;
  }

  _keepFocus(key) {
    this._focusKey = key;
  }

  _restoreFocus() {
    const key = this._focusKey;
    this._focusKey = null;
    if (!key) return;
    const el = [...this._root.querySelectorAll("[data-fk]")].find((e) => e.dataset.fk === key && !e.disabled);
    el?.focus?.();
  }

  _showName(id) {
    return (this._plShows || []).find((s) => s.id === id)?.name || null;
  }

  // --- playlists (Library tab) ---------------------------------------------------
  async _loadPlaylists() {
    if (!this._hass) return;
    if (this._plLoading) {
      this._plAgain = true;
      return;
    }
    this._plLoading = true;
    do {
      this._plAgain = false;
      try {
        const [p, s] = await Promise.all([
          this._hass.callWS({ type: "light_show_studio/playlists/list" }),
          this._hass.callWS({ type: "light_show_studio/shows/list" }),
        ]);
        this._pl = p.playlists || [];
        this._plShows = s.shows || [];
        this._plError = "";
      } catch (err) {
        this._plError = this._err(err, "Couldn't load the playlists.");
        this._pl = this._pl || [];
      }
    } while (this._plAgain);
    this._plLoading = false;
    this._renderPlaylists();
  }

  _plChanged() {
    window.dispatchEvent(new CustomEvent("light-show-studio-playlists-changed"));
  }

  async _plSave(p, patch, focusKey) {
    const next = { ...p, ...patch };
    const prev = { ...p };
    Object.assign(p, patch); // optimistic
    this._plError = "";
    this._keepFocus(focusKey);
    this._renderPlaylists();
    try {
      const r = await this._hass.callWS({
        type: "light_show_studio/playlists/save", playlist_id: p.id, name: next.name, show_ids: next.show_ids,
      });
      Object.assign(p, r.playlist);
      this._plChanged();
    } catch (err) {
      Object.assign(p, prev);
      this._plError = this._err(err, "Couldn't save the playlist.");
    }
    this._keepFocus(focusKey);
    this._renderPlaylists();
  }

  async _plCreate(name) {
    name = name.trim();
    if (!name) {
      this._plError = "Give the playlist a name.";
      return this._renderPlaylists();
    }
    try {
      const r = await this._hass.callWS({ type: "light_show_studio/playlists/save", name, show_ids: [] });
      this._pl.push(r.playlist);
      this._plOpen = r.playlist.id;
      this._plCreating = false;
      this._plError = "";
      this._plChanged();
    } catch (err) {
      this._plError = this._err(err, "Couldn't create the playlist.");
    }
    this._renderPlaylists();
  }

  async _plDelete(p) {
    this._plConfirm = null;
    try {
      await this._hass.callWS({ type: "light_show_studio/playlists/delete", playlist_id: p.id });
      this._pl = this._pl.filter((x) => x.id !== p.id);
      if (this._plOpen === p.id) this._plOpen = null;
      this._plError = "";
      this._plChanged();
    } catch (err) {
      this._plError = this._err(err, "Couldn't delete the playlist.");
    }
    this._renderPlaylists();
  }

  _renderPlaylists() {
    const box = this._plBox;
    if (!box) return;
    const admin = this._isAdmin();
    box.textContent = "";
    const head = this._el("div", "lhead");
    head.append(this._el("h2", "", "Playlists"));
    if (admin && this._pl) {
      const nb = this._btn("New playlist", "btn alt sm", () => {
        this._plCreating = true;
        this._renderPlaylists();
        this._root.querySelector(".pl-new input")?.focus();
      });
      head.append(nb);
    }
    box.append(head);
    box.append(this._el("div", "muted", "A playlist is a named, ordered list of shows (Halloween, Christmas). Pick one on the Play tab or in a schedule."));
    if (this._plError) box.append(this._el("div", "serr", this._plError));
    if (!this._pl) {
      box.append(this._el("div", "muted", "Loading playlists…"));
      return;
    }
    if (this._plCreating && admin) {
      const form = this._el("form", "inl pl-new");
      form.style.marginTop = "10px";
      const input = this._el("input");
      input.type = "text";
      input.maxLength = 60;
      input.placeholder = "Playlist name, e.g. Halloween";
      input.setAttribute("aria-label", "New playlist name");
      form.append(input, this._btn("Create", "btn sm"), this._btn("Cancel", "btn alt sm", () => {
        this._plCreating = false;
        this._renderPlaylists();
      }));
      form.querySelector(".btn").type = "submit";
      form.onsubmit = (e) => {
        e.preventDefault();
        this._plCreate(input.value);
      };
      box.append(form);
    }
    if (!this._pl.length && !this._plCreating) {
      box.append(this._el("div", "muted", admin ? "No playlists yet. Tap New playlist to make one." : "No playlists yet."));
    }
    const list = this._el("div", "");
    list.setAttribute("role", "list");
    for (const p of this._pl) list.append(this._playlistCard(p, admin));
    box.append(list);
    this._restoreFocus();
  }

  _playlistCard(p, admin) {
    const open = this._plOpen === p.id;
    const card = this._el("div", "pl");
    card.setAttribute("role", "listitem");
    const h = this._el("button", "plh");
    h.type = "button";
    h.setAttribute("aria-expanded", String(open));
    h.dataset.fk = `h-${p.id}`;
    const n = p.show_ids.length;
    h.append(this._el("span", "pn", p.name), this._el("span", "muted", `${n} show${n === 1 ? "" : "s"}`));
    const chev = this._el("span", "chev");
    chev.innerHTML = svg("M7.41 8.59 12 13.17l4.59-4.58L18 10l-6 6-6-6z", 22);
    h.append(chev);
    h.onclick = () => {
      this._plOpen = open ? null : p.id;
      this._plConfirm = null;
      this._renderPlaylists();
    };
    card.append(h);
    if (!open) return card;
    const ed = this._el("div", "ple");
    if (admin) {
      const f = this._el("form", "inl");
      f.style.marginTop = "8px";
      const input = this._el("input");
      input.type = "text";
      input.maxLength = 60;
      input.value = p.name;
      input.setAttribute("aria-label", "Playlist name");
      const save = this._btn("Rename", "btn alt sm");
      save.type = "submit";
      f.append(input, save);
      f.onsubmit = (e) => {
        e.preventDefault();
        const name = input.value.trim();
        if (!name) {
          this._plError = "Give the playlist a name.";
          return this._renderPlaylists();
        }
        if (name !== p.name) this._plSave(p, { name }, `h-${p.id}`);
      };
      ed.append(f);
    }
    ed.append(this._el("h3", "", "In this playlist (plays in this order)"));
    if (!p.show_ids.length) ed.append(this._el("div", "muted", admin ? "Empty. Add shows below." : "Empty."));
    p.show_ids.forEach((id, i) => {
      const row = this._el("div", "row2");
      const name = this._showName(id);
      row.append(this._el("span", "rn" + (name ? "" : " gone"), name || `${id} (missing from the library)`));
      if (admin) {
        const mk = (label, path, dis, fn, fk) => {
          const b = this._el("button", "ib2");
          b.type = "button";
          b.innerHTML = svg(path, 22);
          b.title = label;
          b.setAttribute("aria-label", `${label}: ${name || id}`);
          b.disabled = dis;
          b.dataset.fk = fk;
          b.onclick = fn;
          return b;
        };
        const swap = (j) => {
          const ids = [...p.show_ids];
          [ids[i], ids[j]] = [ids[j], ids[i]];
          this._plSave(p, { show_ids: ids }, `${j < i ? "up" : "dn"}-${p.id}-${j}`);
        };
        row.append(
          mk("Move up", "M7.41 15.41 12 10.83l4.59 4.58L18 14l-6-6-6 6z", i === 0, () => swap(i - 1), `up-${p.id}-${i}`),
          mk("Move down", "M7.41 8.59 12 13.17l4.59-4.58L18 10l-6 6-6-6z", i === p.show_ids.length - 1, () => swap(i + 1), `dn-${p.id}-${i}`),
          mk("Remove", "M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z", false,
            () => this._plSave(p, { show_ids: p.show_ids.filter((x) => x !== id) }, `h-${p.id}`), `rm-${p.id}-${i}`)
        );
      }
      ed.append(row);
    });
    if (admin) {
      const rest = (this._plShows || []).filter((s) => !p.show_ids.includes(s.id));
      ed.append(this._el("h3", "", "Add shows"));
      if (!this._plShows?.length) ed.append(this._el("div", "muted", "No shows in the library yet."));
      else if (!rest.length) ed.append(this._el("div", "muted", "Every show is already in this playlist."));
      for (const s of rest) {
        const row = this._el("div", "row2");
        row.append(this._el("span", "rn", s.name));
        const add = this._btn("Add", "btn alt sm", () => this._plSave(p, { show_ids: [...p.show_ids, s.id] }, `ad-${p.id}-${s.id}`));
        add.setAttribute("aria-label", `Add ${s.name}`);
        add.dataset.fk = `ad-${p.id}-${s.id}`;
        row.append(add);
        ed.append(row);
      }
      if (rest.length > 1) {
        const all = this._btn("Add all", "btn alt sm", () => this._plSave(p, { show_ids: [...p.show_ids, ...rest.map((s) => s.id)] }, `h-${p.id}`));
        all.style.marginTop = "8px";
        ed.append(all);
      }
      const acts = this._el("div", "sacts");
      if (this._plConfirm === p.id) {
        acts.append(this._el("span", "muted", `Delete "${p.name}"? The shows stay in the library.`),
          this._btn("Delete", "btn sm warnb", () => this._plDelete(p)),
          this._btn("Cancel", "btn alt sm", () => {
            this._plConfirm = null;
            this._renderPlaylists();
          }));
      } else {
        acts.append(this._btn("Delete playlist", "btn alt sm danger", () => {
          this._plConfirm = p.id;
          this._renderPlaylists();
        }));
      }
      ed.append(acts);
    }
    card.append(ed);
    return card;
  }

  // --- schedule tab ---------------------------------------------------------------
  static _DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  static _MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  static _clock(hhmm) {
    const [h, m] = (hhmm || "00:00").split(":").map(Number);
    return new Date(2000, 0, 1, h || 0, m || 0).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  static _edgeText(e) {
    if (!e) return "";
    if (e.type === "time") return LightShowStudioPanel._clock(e.time);
    const name = e.type === "sunset" ? "Sunset" : "Sunrise";
    const off = Number(e.offset_min) || 0;
    return off ? `${name} ${off < 0 ? "−" : "+"}${Math.abs(off)} min` : name;
  }

  static _mdText(md) {
    const [m, d] = md.split("-").map(Number);
    return `${LightShowStudioPanel._MONTHS[m - 1]} ${d}`;
  }

  // "Fri 6:00 PM" for the coming week, a date after that
  static _when(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    const days = (d - new Date()) / 86400000;
    if (days < 6.5) return `${d.toLocaleDateString([], { weekday: "short" })} ${time}`;
    return `${d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })} ${time}`;
  }

  static _timeOnly(iso) {
    const d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  _subscribeSchedule() {
    if (this._schUnsub || this._schSubscribing || !this._hass?.connection || !this.isConnected) return;
    this._schSubscribing = true;
    this._hass.connection
      .subscribeMessage((m) => {
        this._schSummary = m;
        this._renderSchedBanner();
        this._loadSchedules();
      }, { type: "light_show_studio/schedule/subscribe" })
      .then((unsub) => {
        if (this.isConnected) this._schUnsub = unsub;
        else unsub();
      })
      .catch(() => {})
      .finally(() => {
        this._schSubscribing = false;
      });
  }

  _unsubscribeSchedule() {
    try {
      this._schUnsub?.();
    } catch (e) {
      /* connection already closed */
    }
    this._schUnsub = null;
  }

  async _loadSchedules() {
    if (!this._hass) return;
    if (this._schLoading) {
      this._schAgain = true;
      return;
    }
    this._schLoading = true;
    do {
      this._schAgain = false;
      try {
        const r = await this._hass.callWS({ type: "light_show_studio/schedules/list" });
        this._sch = r.schedules || [];
        this._schPlaylists = r.playlists || [];
        this._schSummary = { status: r.status, active: r.active, next: r.next };
        this._schError = "";
      } catch (err) {
        this._schError = this._err(err, "Couldn't load the schedules.");
        this._sch = this._sch || [];
      }
    } while (this._schAgain);
    this._schLoading = false;
    this._renderSchedBanner();
    if (!this._draft) this._renderSchedules();
  }

  _renderSchedBanner() {
    const b = this._schBanner;
    if (!b) return;
    const sum = this._schSummary;
    b.textContent = "";
    if (!sum) {
      b.append(this._el("div", "muted", this._schError || "Loading the schedule…"));
      return;
    }
    const nx = sum.next;
    let l1 = "Nothing scheduled";
    if (nx) {
      l1 = `Next: ${LightShowStudioPanel._when(nx.start)} · ${nx.playlist || "(no playlist)"}`;
      if (nx.end) l1 += ` (until ${LightShowStudioPanel._timeOnly(nx.end)})`;
      if (nx.schedule) l1 += ` · ${nx.schedule}`;
    }
    b.append(this._el("div", "l1", l1));
    const l2 = this._el("div", "l2");
    const act = sum.active;
    const dot = this._el("span", "sdot " + (sum.status === "running" ? "run" : sum.status === "paused" ? "pause" : ""));
    dot.setAttribute("aria-hidden", "true");
    let text = { idle: "Idle: waiting for the next show", off: "No schedules are turned on" }[sum.status] || sum.status;
    if (sum.status === "running" && act) text = `Running: ${act.schedule}${act.end ? ` until ${LightShowStudioPanel._timeOnly(act.end)}` : ""}`;
    if (sum.status === "paused" && act) text = `Paused: ${act.schedule}. You stopped it, so it stays off until ${act.end ? LightShowStudioPanel._timeOnly(act.end) : "the next start"}`;
    const st = this._el("span", "stt");
    st.append(dot, document.createTextNode(" " + text));
    l2.append(st);
    if (sum.status === "paused" && this._isAdmin()) {
      l2.append(this._btn("Resume", "btn sm", async () => {
        try {
          await this._hass.callWS({ type: "light_show_studio/schedules/resume" });
        } catch (err) {
          this._schError = this._err(err, "Couldn't resume.");
        }
        this._loadSchedules();
      }));
    }
    b.append(l2);
    if (this._schError) b.append(this._el("div", "serr", this._schError));
  }

  _renderSchedules() {
    const box = this._schList;
    if (!box) return;
    const admin = this._isAdmin();
    this._addSched.hidden = !admin || !!this._draft;
    box.textContent = "";
    if (this._schNotice) box.append(this._el("div", "box notice", this._schNotice));
    if (!this._sch) return;
    if (!this._sch.length) {
      const e = this._el("div", "box empty", "No schedules yet.");
      if (admin) {
        e.append(document.createElement("br"));
        e.append(this._btn("Add schedule", "btn", () => this._openEditor()));
      }
      box.append(e);
      return;
    }
    for (const s of this._sch) box.append(this._schedCard(s, admin));
    this._restoreFocus();
  }

  _schedCard(s, admin) {
    const card = this._el("div", "box sc" + (s.enabled ? "" : " off"));
    card.setAttribute("role", "listitem");
    const top = this._el("div", "top");
    top.append(this._el("div", "sn", s.name));
    const tg = this._el("button", "tgl");
    tg.type = "button";
    tg.setAttribute("role", "switch");
    tg.setAttribute("aria-checked", String(s.enabled));
    tg.setAttribute("aria-label", `${s.name} enabled`);
    tg.disabled = !admin;
    tg.dataset.fk = `tg-${s.id}`;
    tg.onclick = async () => {
      s.enabled = !s.enabled; // optimistic
      this._keepFocus(`tg-${s.id}`);
      this._renderSchedules();
      try {
        await this._hass.callWS({ type: "light_show_studio/schedules/toggle", schedule_id: s.id, enabled: s.enabled });
      } catch (err) {
        this._schError = this._err(err, "Couldn't change the schedule.");
      }
      this._keepFocus(`tg-${s.id}`);
      this._loadSchedules();
    };
    top.append(tg);
    card.append(top);
    const body = this._el("div", "sbody");
    const dd = this._el("div", "dd");
    dd.setAttribute("role", "img");
    dd.setAttribute("aria-label", "Days: " + s.days.map((d) => LightShowStudioPanel._DAYS[d]).join(", "));
    LightShowStudioPanel._DAYS.forEach((d, i) => dd.append(this._el("span", s.days.includes(i) ? "on" : "", d[0])));
    body.append(dd);
    const E = LightShowStudioPanel._edgeText;
    body.append(this._el("div", "", s.end ? `${E(s.start)} → ${E(s.end)}` : `${E(s.start)} · plays once`));
    if (s.date_from && s.date_to)
      body.append(this._el("div", "muted", `Season: ${LightShowStudioPanel._mdText(s.date_from)} – ${LightShowStudioPanel._mdText(s.date_to)}`));
    body.append(this._el("div", "", s.playlist ? `Playlist: ${s.playlist}` : "Playlist missing. Edit this schedule to pick one."));
    const tags = this._el("div", "");
    if (s.state === "running") tags.append(this._el("span", "tag run", "Running now"), " ");
    if (s.state === "paused") tags.append(this._el("span", "tag pause", "Paused (stopped by you)"), " ");
    if (s.skipped) tags.append(this._el("span", "tag", "Skipped today"), " ");
    if (tags.childNodes.length) body.append(tags);
    if (s.enabled && s.next_start)
      body.append(this._el("div", "muted", `Next: ${LightShowStudioPanel._when(s.next_start)}${s.next_end ? ` until ${LightShowStudioPanel._timeOnly(s.next_end)}` : ""}`));
    for (const c of s.conflicts || [])
      body.append(this._el("div", "warn", `⚠ Overlaps ${c.name}: whichever starts later takes over.`));
    card.append(body);
    if (admin) {
      const acts = this._el("div", "sacts");
      if (this._schConfirm === s.id) {
        acts.append(this._el("span", "muted", `Delete "${s.name}"?`),
          this._btn("Delete", "btn sm warnb", () => this._schDelete(s)),
          this._btn("Cancel", "btn alt sm", () => {
            this._schConfirm = null;
            this._renderSchedules();
          }));
      } else {
        acts.append(this._btn("Edit", "btn alt sm", () => this._openEditor(s)));
        if (s.enabled) {
          const sk = this._btn(s.skipped ? "Undo skip" : s.state === "running" ? "Stop for today" : "Skip today", "btn alt sm", async () => {
            try {
              await this._hass.callWS({ type: "light_show_studio/schedules/skip_today", schedule_id: s.id, skip: !s.skipped });
            } catch (err) {
              this._schError = this._err(err, "Couldn't skip.");
            }
            this._loadSchedules();
          });
          acts.append(sk);
        }
        acts.append(this._btn("Delete", "btn alt sm danger", () => {
          this._schConfirm = s.id;
          this._renderSchedules();
        }));
      }
      card.append(acts);
    }
    return card;
  }

  async _schDelete(s) {
    this._schConfirm = null;
    try {
      await this._hass.callWS({ type: "light_show_studio/schedules/delete", schedule_id: s.id });
      this._schError = "";
    } catch (err) {
      this._schError = this._err(err, "Couldn't delete the schedule.");
    }
    this._loadSchedules();
  }

  // --- schedule editor ------------------------------------------------------------
  _speakerIds() {
    const own = this._findPlayerId();
    return Object.keys(this._hass.states)
      .filter((id) => id.startsWith("media_player.") && id !== own)
      .sort((a, b) => this._fname(a).localeCompare(this._fname(b)));
  }

  _fname(id) {
    return this._hass.states[id]?.attributes.friendly_name || id;
  }

  _effectNames() {
    const names = new Set();
    for (const id of this._lights()) for (const n of this._hass.states[id]?.attributes.effect_list || []) if (n !== SHOW) names.add(n);
    return [...names];
  }

  _openEditor(s) {
    this._schNotice = "";
    this._draft = s
      ? JSON.parse(JSON.stringify({
          id: s.id, name: s.name, enabled: s.enabled, days: s.days, start: s.start, end: s.end || null,
          season: !!(s.date_from && s.date_to), date_from: s.date_from || "11-25", date_to: s.date_to || "01-06",
          playlist_id: s.playlist_id, speakers: s.speakers || [], volume: s.volume ?? null, shuffle: !!s.shuffle,
          repeat: s.repeat !== false, at_end: s.at_end || "off", end_effect: s.end_effect || "",
        }))
      : {
          id: null, name: "", enabled: true, days: [0, 1, 2, 3, 4, 5, 6], start: { type: "time", time: "18:00", offset_min: 0 },
          end: { type: "time", time: "22:00", offset_min: 0 }, season: false, date_from: "11-25", date_to: "01-06",
          playlist_id: this._schPlaylists?.[0]?.id || "", speakers: [], volume: null, shuffle: false, repeat: true,
          at_end: "off", end_effect: "",
        };
    if (!this._schPlaylists?.length) this._loadSchedules();
    this._draftError = "";
    this._renderEditor();
    this._renderSchedules();
    this._sed.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    this._sed.querySelector("input")?.focus();
  }

  _closeEditor() {
    this._draft = null;
    this._sed.hidden = true;
    this._sed.textContent = "";
    this._renderSchedules();
  }

  _field(label, ...kids) {
    const f = this._el("div", "fld");
    f.append(this._el("span", "lb", label), ...kids);
    return f;
  }

  _edgeEditor(label, key, allowNone) {
    const d = this._draft;
    const e = d[key];
    const wrap = this._el("div", "fld");
    wrap.append(this._el("span", "lb", label));
    const opts = [...(allowNone ? [["none", "Play once"]] : []), ["time", "Time"], ["sunset", "Sunset"], ["sunrise", "Sunrise"]];
    wrap.append(this._seg(opts, e ? e.type : "none", label, (v) => {
      if (v === "none") d[key] = null;
      else d[key] = { type: v, time: e?.time || (key === "start" ? "18:00" : "22:00"), offset_min: e?.offset_min || 0 };
      this._renderEditor();
    }));
    if (e) {
      const row = this._el("div", "edge-in");
      if (e.type === "time") {
        const t = this._el("input");
        t.type = "time";
        t.value = e.time || "";
        t.required = true;
        t.setAttribute("aria-label", `${label} time`);
        t.oninput = () => (e.time = t.value);
        row.append(t);
      } else {
        const n = this._el("input");
        n.type = "number";
        n.step = 5;
        n.min = -720;
        n.max = 720;
        n.value = e.offset_min || 0;
        n.style.width = "90px";
        n.setAttribute("aria-label", `${label} ${e.type} offset in minutes`);
        n.oninput = () => (e.offset_min = n.value === "" ? 0 : Math.round(Number(n.value)));
        row.append(n, this._el("span", "note", `minutes after ${e.type} (negative = before)`));
      }
      wrap.append(row);
    }
    return wrap;
  }

  _renderEditor() {
    const d = this._draft;
    const ed = this._sed;
    ed.hidden = false;
    ed.textContent = "";
    ed.append(this._el("h2", "", d.id ? "Edit schedule" : "New schedule"));
    const form = this._el("form", "");
    form.noValidate = true;
    form.onsubmit = (e) => {
      e.preventDefault();
      this._saveDraft();
    };
    // name
    const name = this._el("input");
    name.type = "text";
    name.maxLength = 60;
    name.value = d.name;
    name.placeholder = "e.g. Christmas evenings";
    name.oninput = () => (d.name = name.value);
    form.append(this._field("Name", name));
    // days
    const days = this._el("div", "fld");
    days.append(this._el("span", "lb", "Days"));
    const ds = this._el("div", "dsel");
    ds.setAttribute("role", "group");
    ds.setAttribute("aria-label", "Days of the week");
    const FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
    LightShowStudioPanel._DAYS.forEach((dn, i) => {
      const b = this._btn(dn[0], "", () => {
        d.days = d.days.includes(i) ? d.days.filter((x) => x !== i) : [...d.days, i].sort();
        b.setAttribute("aria-pressed", String(d.days.includes(i)));
      });
      b.title = FULL[i];
      b.setAttribute("aria-label", FULL[i]);
      b.setAttribute("aria-pressed", String(d.days.includes(i)));
      ds.append(b);
    });
    days.append(ds);
    const quick = this._el("div", "inl");
    quick.append(this._btn("Every day", "btn alt sm", () => { d.days = [0, 1, 2, 3, 4, 5, 6]; this._renderEditor(); }),
      this._btn("Weekends", "btn alt sm", () => { d.days = [4, 5, 6]; this._renderEditor(); }));
    days.append(quick);
    form.append(days);
    form.append(this._edgeEditor("Start", "start", false));
    form.append(this._edgeEditor("End", "end", true));
    // season
    const season = this._el("div", "fld");
    const sc = this._el("label", "chk");
    const scb = this._el("input");
    scb.type = "checkbox";
    scb.checked = d.season;
    scb.onchange = () => {
      d.season = scb.checked;
      this._renderEditor();
    };
    sc.append(scb, document.createTextNode("Only during a season (repeats every year)"));
    season.append(sc);
    if (d.season) {
      const mk = (label, key) => {
        const [m, dd] = d[key].split("-").map(Number);
        const wrap = this._el("div", "md");
        wrap.append(this._el("span", "", label));
        const ms = this._el("select");
        ms.setAttribute("aria-label", `${label} month`);
        LightShowStudioPanel._MONTHS.forEach((mn, i) => {
          const o = this._el("option", "", mn);
          o.value = i + 1;
          ms.append(o);
        });
        ms.value = m;
        const ds2 = this._el("select");
        ds2.setAttribute("aria-label", `${label} day`);
        const fill = () => {
          const max = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][Number(ms.value) - 1];
          const cur = Number(ds2.value) || dd;
          ds2.textContent = "";
          for (let i = 1; i <= max; i++) {
            const o = this._el("option", "", String(i));
            o.value = i;
            ds2.append(o);
          }
          ds2.value = Math.min(cur, max);
        };
        fill();
        const upd = () => (d[key] = `${String(ms.value).padStart(2, "0")}-${String(ds2.value).padStart(2, "0")}`);
        ms.onchange = () => {
          fill();
          upd();
        };
        ds2.onchange = upd;
        wrap.append(ms, ds2);
        return wrap;
      };
      const row = this._el("div", "seas");
      row.append(mk("From", "date_from"), mk("To", "date_to"));
      season.append(row, this._el("div", "muted", "A season can cross New Year, like Nov 25 to Jan 6."));
    }
    form.append(season);
    // playlist
    const pl = this._el("select");
    pl.setAttribute("aria-label", "Playlist");
    const lists = this._schPlaylists || [];
    for (const p of lists) {
      const o = this._el("option", "", `${p.name} (${p.show_ids.length})`);
      o.value = p.id;
      pl.append(o);
    }
    if (d.playlist_id && !lists.some((p) => p.id === d.playlist_id)) {
      const o = this._el("option", "", "(missing playlist)");
      o.value = d.playlist_id;
      pl.append(o);
    }
    if (!pl.options.length) pl.append(Object.assign(this._el("option", "", "No playlists: make one in the Library tab"), { value: "" }));
    pl.value = d.playlist_id;
    d.playlist_id = pl.value;
    pl.onchange = () => (d.playlist_id = pl.value);
    form.append(this._field("Playlist", pl));
    // speakers
    const spk = this._el("details", "spk");
    if (d.speakers.length) spk.open = true;
    const sum = this._el("summary", "", d.speakers.length ? `Speakers (${d.speakers.length} selected)` : "Speakers (the player's current output)");
    spk.append(sum);
    const sl = this._el("div", "spk-l");
    const ids = this._speakerIds();
    d.speakers.filter((id) => !ids.includes(id)).forEach((id) => ids.unshift(id));
    if (!ids.length) sl.append(this._el("div", "muted", "No speakers found"));
    for (const id of ids) {
      const l = this._el("label", "chk");
      const cb = this._el("input");
      cb.type = "checkbox";
      cb.checked = d.speakers.includes(id);
      cb.onchange = () => {
        d.speakers = d.speakers.filter((x) => x !== id);
        if (cb.checked) d.speakers.push(id);
        sum.textContent = d.speakers.length ? `Speakers (${d.speakers.length} selected)` : "Speakers (the player's current output)";
      };
      l.append(cb, this._el("span", "", this._fname(id)));
      sl.append(l);
    }
    spk.append(sl, this._el("div", "muted", "Leave all unchecked to use whatever the player is set to. The first speaker you pick leads."));
    form.append(spk);
    // volume
    const vol = this._el("div", "fld");
    const vl = this._el("label", "chk");
    const vcb = this._el("input");
    vcb.type = "checkbox";
    vcb.checked = d.volume !== null;
    vcb.onchange = () => {
      d.volume = vcb.checked ? (d.volume ?? 40) : null;
      this._renderEditor();
    };
    vl.append(vcb, document.createTextNode("Set the speaker volume when it starts"));
    vol.append(vl);
    if (d.volume !== null) {
      const row = this._el("div", "vrow");
      const r = this._el("input");
      r.type = "range";
      r.min = 0;
      r.max = 100;
      r.value = d.volume;
      r.setAttribute("aria-label", "Volume");
      const bv = this._el("span", "bv", `${d.volume}%`);
      r.oninput = () => {
        d.volume = Number(r.value);
        bv.textContent = `${d.volume}%`;
      };
      row.append(r, bv);
      vol.append(row);
    }
    form.append(vol);
    // shuffle / repeat
    const opts = this._el("div", "fld");
    const chk = (text, key) => {
      const l = this._el("label", "chk");
      const c = this._el("input");
      c.type = "checkbox";
      c.checked = !!d[key];
      c.onchange = () => (d[key] = c.checked);
      l.append(c, document.createTextNode(text));
      return l;
    };
    opts.append(chk("Shuffle", "shuffle"));
    if (d.end) opts.append(chk("Repeat the playlist while the window is open", "repeat"));
    form.append(opts);
    // at end
    const at = this._el("div", "fld");
    at.append(this._el("span", "lb", "When it ends"));
    at.append(this._seg([["off", "Lights off"], ["effect", "Effect"], ["leave", "Leave as is"]], d.at_end, "When it ends", (v) => {
      d.at_end = v;
      this._renderEditor();
    }));
    if (d.at_end === "effect") {
      const fx = this._el("select");
      fx.setAttribute("aria-label", "Effect at the end");
      const names = this._effectNames();
      if (d.end_effect && !names.includes(d.end_effect)) names.unshift(d.end_effect);
      if (!names.length) fx.append(Object.assign(this._el("option", "", "No effects found: is a light online?"), { value: "" }));
      for (const n of names) fx.append(Object.assign(this._el("option", "", n), { value: n }));
      if (!d.end_effect && names.length) d.end_effect = names[0];
      fx.value = d.end_effect;
      fx.onchange = () => (d.end_effect = fx.value);
      at.append(fx);
    }
    form.append(at);
    const err = this._el("div", "serr", this._draftError);
    err.setAttribute("role", "alert");
    err.hidden = !this._draftError;
    form.append(err);
    const btns = this._el("div", "btnrow");
    const save = this._btn("Save", "btn sm");
    save.type = "submit";
    save.className = "btn sm save-sched";
    btns.append(save, this._btn("Cancel", "btn alt sm", () => this._closeEditor()));
    form.append(btns);
    ed.append(form);
  }

  async _saveDraft() {
    const d = this._draft;
    const fail = (t) => {
      this._draftError = t;
      const e = this._sed.querySelector(".serr");
      if (e) {
        e.textContent = t;
        e.hidden = !t;
      }
    };
    const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
    const name = d.name.trim();
    if (!name) return fail("Give the schedule a name.");
    if (!d.days.length) return fail("Pick at least one day.");
    if (d.start.type === "time" && !HHMM.test(d.start.time || "")) return fail("Enter a start time.");
    if (d.end && d.end.type === "time" && !HHMM.test(d.end.time || "")) return fail("Enter an end time.");
    if (!d.playlist_id) return fail("Pick a playlist (make one in the Library tab first).");
    if (d.at_end === "effect" && !d.end_effect) return fail("Pick the effect to show at the end.");
    const edge = (e) => (e.type === "time" ? { type: "time", time: e.time, offset_min: 0 } : { type: e.type, offset_min: Number(e.offset_min) || 0 });
    const msg = {
      type: "light_show_studio/schedules/save", name, enabled: d.enabled, days: d.days, start: edge(d.start),
      end: d.end ? edge(d.end) : null, date_from: d.season ? d.date_from : null, date_to: d.season ? d.date_to : null,
      playlist_id: d.playlist_id, speakers: d.speakers, volume: d.volume, shuffle: d.shuffle, repeat: d.end ? d.repeat : false,
      at_end: d.at_end,
    };
    if (d.id) msg.schedule_id = d.id;
    if (d.at_end === "effect") msg.end_effect = d.end_effect;
    const btn = this._sed.querySelector(".save-sched");
    if (btn) btn.disabled = true;
    fail("");
    try {
      const r = await this._hass.callWS(msg);
      const c = r.schedule?.conflicts || [];
      this._schNotice = c.length ? `Saved. It overlaps ${c.map((x) => x.name).join(", ")}: whichever window starts later takes over.` : "";
      this._closeEditor();
      this._loadSchedules();
    } catch (err) {
      fail(this._err(err, "Couldn't save the schedule."));
      if (btn) btn.disabled = false;
    }
  }

  // --- live updates ------------------------------------------------------
  _update() {
    const player = this._findPlayer();
    const shows = (player?.attributes.queue || []).length;
    const status = { lights: this._strands ? this._strands.length > 0 : this._lights().length > 0, player: !!player, shows: shows > 0 };
    if (this._tab === null) {
      const saved = stored.get();
      const mapped = saved in OLD_TABS ? OLD_TABS[saved] : saved;
      const first = !status.lights ? "lights" : !status.shows ? "library" : "play";
      this._select(TABS.some(([id]) => id === mapped) ? mapped : first, false);
    }
    if (this._tab === "studio") this._renderStudioControls();
    for (const el of [this._player, this._library, this._adder]) this._feed(el);
    this._renderBanner(status);
  }

  _findPlayerId() {
    return Object.keys(this._hass.states).find(
      (k) => k.startsWith("media_player.") && this._hass.states[k].attributes.queue !== undefined
    );
  }

  _findPlayer() {
    const id = this._findPlayerId();
    return id ? this._hass.states[id] : null;
  }

  // first-run banner: only while lights, the player or shows are missing; dismissing it hides it until the missing set changes
  _renderBanner(status) {
    const todo = [
      !status.lights && ["Add a light", "Add a light", () => this._navigate(INTEGRATION_PATH)],
      !status.player && ["Set up the Light Show Player", "Set up the player", () => this._navigate(INTEGRATION_PATH)],
      status.player && !status.shows && ["Add your first show", "Add shows", () => {
        this._select("library", true);
        this._addShows.open = true;
      }],
    ].filter(Boolean);
    const sig = todo.map((t) => t[0]).join("|");
    if (!todo.length || storedBanner.get() === sig) {
      this._banner.hidden = true;
      this._bannerSig = null;
      return;
    }
    this._banner.hidden = false;
    if (sig === this._bannerSig) return;
    this._bannerSig = sig;
    this._banner.textContent = "";
    const head = this._el("div", "bh");
    head.append(this._el("h2", "", "Let's get set up"));
    const x = this._el("button", "x");
    x.type = "button";
    x.setAttribute("aria-label", "Dismiss");
    x.innerHTML = svg("M19 6.4 17.6 5 12 10.6 6.4 5 5 6.4 10.6 12 5 17.6 6.4 19 12 13.4 17.6 19 19 17.6 13.4 12z", 20);
    x.onclick = () => {
      storedBanner.set(sig);
      this._banner.hidden = true;
    };
    head.append(x);
    const ul = this._el("ul");
    for (const [label, btn, go] of todo) {
      const li = this._el("li");
      const b = this._el("button", "btn sm", btn);
      b.type = "button";
      b.onclick = go;
      li.append(this._el("span", "", label), b);
      ul.append(li);
    }
    this._banner.append(head, ul);
  }

  _navigate(path) {
    history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
  }
}

customElements.define("light-show-studio-panel", LightShowStudioPanel);
