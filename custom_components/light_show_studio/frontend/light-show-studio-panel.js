// Light Show Studio sidebar app: Play / Library / Schedule / Lights / Map / Studio in one page. Ships with light_show_studio.
// Reuses the two cards, so light-show-studio-card.js and light-show-studio-player-card.js must be loaded too.
// Custom element: light-show-studio-panel (HA sets hass, narrow, route, panel on it).

const TABS = [
  ["play", "Play", "M8 5v14l11-7z"],
  ["library", "Library", "M4 6H2v14c0 1.1.9 2 2 2h14v-2H4zm16-4H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-1 9H9V9h10zm-4 4H9v-2h6zm4-8H9V5h10z"],
  ["schedule", "Schedule", "M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z"],
  ["lights", "Lights", "M12 2a7 7 0 0 0-4 12.7V17a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-2.3A7 7 0 0 0 12 2zM9 20h6v1a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1z"],
  ["map", "Map", "M20.5 3l-.16.03L15 5.1 9 3 3.36 4.9c-.21.07-.36.25-.36.48V20.5c0 .28.22.5.5.5l.16-.03L9 18.9l6 2.1 5.64-1.9c.21-.07.36-.25.36-.48V3.5c0-.28-.22-.5-.5-.5zM15 19l-6-2.11V5l6 2.11V19z"],
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
// mirrors PATTERNS in patterns.py: name -> [colours as r,g,b, bulbs per colour]; no colours = rainbow gradient
const PATTERN_COLOURS = {
  "Classic Multicolor": [[[255, 0, 0], [255, 150, 0], [0, 200, 0], [0, 40, 255], [255, 70, 0], [255, 20, 120]], 1],
  "Warm White": [[[255, 150, 40]], 1],
  "Cool White": [[[200, 220, 255]], 1],
  "Red & Green": [[[255, 0, 0], [0, 200, 0]], 1],
  "Red, Green & White": [[[255, 0, 0], [0, 200, 0], [255, 200, 140]], 1],
  "Candy Cane": [[[255, 0, 0], [255, 255, 255]], 2],
  "Blue & White": [[[0, 40, 255], [200, 220, 255]], 1],
  "Gold & White": [[[255, 140, 0], [255, 200, 140]], 1],
  Pastel: [[[255, 120, 170], [120, 200, 255], [255, 220, 90], [150, 255, 140], [190, 130, 255]], 1],
  Halloween: [[[255, 70, 0], [140, 0, 255], [40, 255, 0]], 1],
  "Red, White & Blue": [[[255, 0, 0], [255, 255, 255], [0, 40, 255]], 1],
  Valentine: [[[255, 0, 0], [255, 20, 110], [255, 200, 200]], 1],
  Rainbow: [[], 1],
};
const RAINBOW_CSS = "linear-gradient(90deg,#f00,#ff0,#0f0,#0ff,#00f,#f0f,#f00)";
const EFFECT_INFO = {
  Steady: "Colours stay on, no movement",
  Twinkle: "Each bulb fades up and down on its own",
  Chase: "The pattern glides along the string",
  "Twinkle Swap": "Bulbs dim out and return in another colour",
  Marquee: "Every third bulb lit, stepping along like a theatre sign",
  Fade: "The whole string breathes in and out",
  Sparkle: "Quick white glints over the pattern",
  Alternate: "Odd and even bulbs take turns",
  Wave: "A soft wave of brightness travels along",
};
// the slider only means something while an effect is moving
const STILL = new Set(["Steady"]);
const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;

const PANEL_ICON = {
  map: "M20.5 3l-.16.03L15 5.1 9 3 3.36 4.9c-.21.07-.36.25-.36.48V20.5c0 .28.22.5.5.5l.16-.03L9 18.9l6 2.1 5.64-1.9c.21-.07.36-.25.36-.48V3.5c0-.28-.22-.5-.5-.5zM15 19l-6-2.11V5l6 2.11V19z",
  bulb: "M12 2a7 7 0 0 0-4 12.7V17a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-2.3A7 7 0 0 0 12 2zM9 20h6v1a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1z",
  add: "M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6z",
  trash: "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z",
  up: "M7.41 15.41 12 10.83l4.59 4.58L18 14l-6-6-6 6z",
  down: "M7.41 8.59 12 13.17l4.59-4.58L18 10l-6 6-6-6z",
  close: "M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z",
  swap: "M6.99 11 3 15l3.99 4v-3H14v-2H6.99v-3zM21 9l-3.99-4v3H10v2h7.01v3L21 9z",
  play: "M8 5v14l11-7z",
  stop: "M6 6h12v12H6z",
  save: "M17 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7l-4-4zm-5 16a3 3 0 1 1 0-6 3 3 0 0 1 0 6zm3-10H5V5h10v4z",
  layers: "M11.99 18.54l-7.37-5.73L3 14.07l9 7 9-7-1.63-1.27-7.38 5.74zM12 16l7.36-5.73L21 9l-9-7-9 7 1.63 1.27L12 16z",
  route: "M19 15.18V7c0-2.21-1.79-4-4-4s-4 1.79-4 4v10c0 1.1-.9 2-2 2s-2-.9-2-2V8.82C8.16 8.4 9 7.3 9 6c0-1.66-1.34-3-3-3S3 4.34 3 6c0 1.3.84 2.4 2 2.82V17c0 2.21 1.79 4 4 4s4-1.79 4-4V7c0-1.1.9-2 2-2s2 .9 2 2v8.18c-1.16.41-2 1.51-2 2.82 0 1.66 1.34 3 3 3s3-1.34 3-3c0-1.3-.84-2.4-2-2.82z",
  palette: "M12 3a9 9 0 0 0 0 18c1.1 0 2-.9 2-2 0-.5-.2-1-.5-1.3-.3-.4-.5-.8-.5-1.3 0-1.1.9-2 2-2h2.4c3 0 5.1-2.3 5.1-5.2C22 6.2 17.5 3 12 3zM6.5 12a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm3-4a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm5 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm3 4a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3z",
  gear: "M19.43 12.98c.04-.32.07-.64.07-.98s-.03-.66-.07-.98l2.11-1.65c.19-.15.24-.42.12-.64l-2-3.46a.5.5 0 0 0-.61-.22l-2.49 1c-.52-.4-1.08-.73-1.69-.98l-.38-2.65A.49.49 0 0 0 14 2h-4c-.25 0-.46.18-.49.42l-.38 2.65c-.61.25-1.17.59-1.69.98l-2.49-1a.5.5 0 0 0-.61.22l-2 3.46c-.13.22-.07.49.12.64l2.11 1.65c-.04.32-.07.65-.07.98s.03.66.07.98l-2.11 1.65c-.19.15-.24.42-.12.64l2 3.46c.12.22.39.3.61.22l2.49-1c.52.4 1.08.73 1.69.98l.38 2.65c.03.24.24.42.49.42h4c.25 0 .46-.18.49-.42l.38-2.65c.61-.25 1.17-.59 1.69-.98l2.49 1c.23.09.49 0 .61-.22l2-3.46c.12-.22.07-.49-.12-.64l-2.11-1.65zM12 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7z",
  list: "M3 13h2v-2H3v2zm0 4h2v-2H3v2zm0-8h2V7H3v2zm4 4h14v-2H7v2zm0 4h14v-2H7v2zM7 7v2h14V7H7z",
  clock: "M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z",
  queue: "M15 6H3v2h12V6zm0 4H3v2h12v-2zM3 16h8v-2H3v2zM17 6v8.18c-.31-.11-.65-.18-1-.18-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3V8h3V6h-5z",
  upload: "M9 16h6v-6h4l-7-7-7 7h4zm-4 2h14v2H5z",
  link: "M3.9 12c0-1.71 1.39-3.1 3.1-3.1h4V7H7c-2.76 0-5 2.24-5 5s2.24 5 5 5h4v-1.9H7c-1.71 0-3.1-1.39-3.1-3.1zM8 13h8v-2H8v2zm9-6h-4v1.9h4c1.71 0 3.1 1.39 3.1 3.1s-1.39 3.1-3.1 3.1h-4V17h4c2.76 0 5-2.24 5-5s-2.24-5-5-5z",
  target: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zm8.94 3A8.994 8.994 0 0 0 13 3.06V1h-2v2.06A8.994 8.994 0 0 0 3.06 11H1v2h2.06A8.994 8.994 0 0 0 11 20.94V23h2v-2.06A8.994 8.994 0 0 0 20.94 13H23v-2h-2.06zM12 19c-3.87 0-7-3.13-7-7s3.13-7 7-7 7 3.13 7 7-3.13 7-7 7z",
};

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
    this._stripEls = new Set(); // canvases showing a light's live colours
    this._frames = {}; // entity id -> last frame event
    this._subs = new Map(); // entity id -> unsubscribe function (or a pending marker)
    this._map = null;
    this._mapSel = { zone: null, run: null };
  }

  connectedCallback() {
    this._startPolling();
    this._syncWatch();
    if (this._tab === "schedule") this._subscribeSchedule();
  }

  disconnectedCallback() {
    clearInterval(this._poll);
    this._poll = null;
    this._unsubscribeSchedule();
    this._unsubscribeMap();
    this._watchFrames([]);
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
        /* ---- design tokens: HA theme variables with fallbacks, so light and dark both work ---- */
        :host { --lss-bg: var(--primary-background-color, #f2f3f7); --lss-card: var(--card-background-color, #fff);
                --lss-text: var(--primary-text-color, #1d2129); --lss-mut: var(--secondary-text-color, #667085);
                --lss-line: var(--divider-color, #e1e4ea); --lss-acc: var(--primary-color, #03a9f4);
                --lss-onacc: var(--text-primary-color, #fff);
                --lss-ok: var(--success-color, #2e9e5b); --lss-err: var(--error-color, #db4437); --lss-warn: var(--warning-color, #d98a00);
                --lss-soft: rgba(127,127,127,.12); --lss-soft2: rgba(127,127,127,.2);
                --lss-tint: rgba(127,127,127,.14);
                --r1: 8px; --r2: 12px; --r3: 16px; --ease: cubic-bezier(.2,.8,.2,1);
                --shadow: 0 1px 2px rgba(0,0,0,.06), 0 4px 14px rgba(0,0,0,.05);
                display: flex; flex-direction: column; height: 100%; min-height: 100vh; box-sizing: border-box;
                background: var(--lss-bg); color: var(--lss-text);
                font-family: var(--paper-font-body1_-_font-family, Roboto, "Segoe UI", system-ui, sans-serif);
                -webkit-text-size-adjust: 100%; }
        @supports (color: color-mix(in srgb, red 10%, blue)) {
          :host { --lss-tint: color-mix(in srgb, var(--lss-acc) 14%, transparent); }
        }
        @supports (height: 100dvh) { :host { min-height: 100dvh; } }
        * { box-sizing: border-box; }
        .root { display: flex; flex-direction: column; flex: 1; min-height: 0; height: 100%; }
        [hidden] { display: none !important; }
        button { font: inherit; color: inherit; cursor: pointer; border: none; background: none; padding: 0;
                 -webkit-tap-highlight-color: transparent; }
        :focus-visible { outline: 2px solid var(--lss-acc); outline-offset: 2px; }
        svg { display: block; flex: none; }
        @keyframes lss-in { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
        @keyframes lss-shimmer { 0% { background-position: 100% 0; } 100% { background-position: -100% 0; } }
        @keyframes lss-pulse { 0% { box-shadow: 0 0 0 0 rgba(67,200,110,.55); } 100% { box-shadow: 0 0 0 8px rgba(67,200,110,0); } }
        .tabin { animation: lss-in .22s var(--ease); }
        @media (prefers-reduced-motion: reduce) {
          *, *::before, *::after { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
        }
        /* ---- header ---- */
        header { display: flex; align-items: center; gap: 4px; min-height: 64px; padding: 0 8px 0 16px; flex: none;
                 background: var(--app-header-background-color, var(--lss-acc));
                 color: var(--app-header-text-color, var(--lss-onacc)); position: relative; z-index: 6;
                 box-shadow: 0 2px 8px rgba(0,0,0,.18); }
        .narrow header { padding-left: 4px; min-height: 60px; }
        .menu { width: 48px; height: 48px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; flex: none; }
        .menu:hover { background: rgba(255,255,255,.15); }
        .brand { display: flex; align-items: center; gap: 12px; min-width: 0; margin-right: 12px; }
        .logo { width: 40px; height: 40px; border-radius: 12px; flex: none; display: inline-flex; align-items: center; justify-content: center;
                background: rgba(255,255,255,.2); box-shadow: inset 0 0 0 1px rgba(255,255,255,.25); }
        .bt { min-width: 0; }
        h1 { font-size: 19px; line-height: 1.15; font-weight: 600; letter-spacing: .01em; margin: 0; white-space: nowrap; }
        .hstat { display: flex; align-items: center; gap: 7px; font-size: 12.5px; opacity: .92; margin-top: 2px; min-width: 0; }
        .hstat .htxt { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .hdot { width: 8px; height: 8px; border-radius: 50%; flex: none; background: rgba(255,255,255,.5); }
        .hdot.on { background: #ffd54a; }
        .hdot.run { background: #5be38b; animation: lss-pulse 1.6s ease-out infinite; }
        nav { display: flex; margin-left: auto; align-self: stretch; }
        nav button { position: relative; display: inline-flex; align-items: center; gap: 8px; padding: 0 14px; height: 100%;
                     font-size: 13px; font-weight: 600; letter-spacing: .03em; opacity: .75; transition: opacity .15s, background .15s; }
        nav button .ic { display: inline-flex; }
        nav button[aria-selected=true] { opacity: 1; box-shadow: inset 0 -3px 0 currentColor; }
        nav button:hover { opacity: 1; background: rgba(255,255,255,.1); }
        /* ---- page ---- */
        main { flex: 1; overflow-x: hidden; overflow-y: auto; padding: 20px 16px 28px; width: 100%; max-width: 960px; margin: 0 auto; }
        section > * + * { margin-top: 16px; }
        section:not([hidden]) { animation: lss-in .22s var(--ease); }
        .box { background: var(--lss-card); border-radius: var(--r3); border: 1px solid var(--lss-line); padding: 16px;
               line-height: 1.5; box-shadow: var(--shadow); min-width: 0; }
        .box h2, .lhead h2 { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 600; margin: 0 0 12px; letter-spacing: .005em; }
        .hi { width: 30px; height: 30px; border-radius: 10px; flex: none; display: inline-flex; align-items: center; justify-content: center;
              background: var(--lss-tint); color: var(--lss-acc); }
        .hi svg { width: 18px; height: 18px; }
        .box ol { margin: 0; padding-left: 22px; }
        .lhead, .banner .bh { display: flex; align-items: center; justify-content: space-between; gap: 8px 12px; flex-wrap: wrap; }
        .lhead h2 { margin: 0; font-size: 20px; font-weight: 600; }
        .lhead .hi { width: 34px; height: 34px; }
        .lhead .hsub { flex: 1 0 100%; margin-top: -4px; color: var(--lss-mut); font-size: 13px; }
        .banner { border-color: var(--lss-acc); background: linear-gradient(180deg, var(--lss-tint), transparent 70%), var(--lss-card); }
        .banner ul { list-style: none; margin: 4px 0 0; padding: 0; }
        .banner li { display: flex; align-items: center; justify-content: space-between; gap: 8px 12px; flex-wrap: wrap; padding: 8px 0; }
        .banner .x { width: 44px; height: 44px; margin: -6px -8px 0 0; border-radius: 50%; flex: none; display: inline-flex; align-items: center; justify-content: center; }
        .banner .x:hover { background: var(--lss-soft); }
        details.add > summary { cursor: pointer; font-size: 16px; font-weight: 600; min-height: 44px; display: flex; align-items: center; gap: 10px; }
        details.add[open] > summary { margin-bottom: 8px; }
        details.add .ulcard > * { display: block; margin: 12px 0 0; }
        .libcard > * { display: block; margin: 0; }
        .box li + li { margin-top: 6px; }
        .muted { color: var(--lss-mut); font-size: 13px; }
        code { background: var(--lss-soft); padding: 1px 6px; border-radius: 6px; font-size: .92em; }
        /* ---- buttons ---- */
        .btn { font-size: 14px; font-weight: 600; letter-spacing: .01em; padding: 8px 18px; min-height: 44px; border-radius: var(--r2);
               background: var(--lss-acc); color: var(--lss-onacc); display: inline-flex; align-items: center; justify-content: center; gap: 8px;
               box-shadow: 0 1px 2px rgba(0,0,0,.15); transition: filter .15s, transform .1s, background .15s; }
        .btn:hover:not(:disabled) { filter: brightness(1.08); }
        .btn:active:not(:disabled) { transform: scale(.97); }
        .btn.alt { background: var(--lss-soft); color: var(--lss-text); box-shadow: inset 0 0 0 1px var(--lss-line); }
        .btn.alt:hover:not(:disabled) { background: var(--lss-soft2); filter: none; }
        .btn.sm { min-height: 40px; padding: 6px 14px; font-size: 13.5px; }
        .btn:disabled { opacity: .45; cursor: default; box-shadow: none; }
        .btn.danger, .btn.rm { color: var(--lss-err); }
        .btn.rm { margin-left: auto; }
        .btn.warnb { background: var(--lss-err); color: #fff; }
        .btn svg { width: 18px; height: 18px; }
        .narrow .btn.sm { min-height: 44px; }
        /* ---- empty + loading states ---- */
        .empty { text-align: center; padding: 32px 20px; color: var(--lss-mut); display: flex; flex-direction: column; align-items: center; gap: 6px; }
        .empty .eic { width: 60px; height: 60px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
                      background: var(--lss-tint); color: var(--lss-acc); margin-bottom: 6px; }
        .empty .eic svg { width: 30px; height: 30px; }
        .empty .et { font-size: 17px; font-weight: 600; color: var(--lss-text); }
        .empty .ed { font-size: 14px; max-width: 34em; }
        .empty .btn { margin-top: 12px; }
        .skel { display: flex; flex-direction: column; gap: 12px; }
        .skel i { display: block; height: 64px; border-radius: var(--r2);
                  background: linear-gradient(90deg, var(--lss-soft) 30%, var(--lss-soft2) 50%, var(--lss-soft) 70%); background-size: 300% 100%;
                  animation: lss-shimmer 1.4s linear infinite; }
        .skel i:nth-child(2) { height: 48px; opacity: .8; } .skel i:nth-child(3) { height: 48px; opacity: .6; }
        /* ---- status pills + live strip ---- */
        .sdot { flex: none; width: 10px; height: 10px; border-radius: 50%; background: var(--disabled-text-color, #9e9e9e); }
        .sdot.on, .sdot.run { background: var(--lss-ok); }
        .sdot.off { background: var(--lss-err); }
        .sdot.pause { background: var(--lss-warn); }
        .state, .tag, .pill { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; font-weight: 600; line-height: 1.4; padding: 1px 10px;
                              border-radius: 999px; background: var(--lss-soft); color: var(--lss-mut); white-space: nowrap; }
        .state.on, .tag.run, .pill.ok { background: rgba(67,176,100,.16); color: var(--lss-ok); }
        .state.off, .pill.err { background: rgba(219,68,55,.14); color: var(--lss-err); }
        .tag.pause, .pill.warn { background: rgba(240,160,0,.16); color: var(--lss-warn); }
        .pill.acc { background: var(--lss-tint); color: var(--lss-acc); }
        .pill.run::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--lss-ok); animation: lss-pulse 1.6s ease-out infinite; }
        .strip { display: block; width: 100%; height: 12px; border-radius: 6px; background: #0c0e13; box-shadow: inset 0 0 0 1px rgba(127,127,127,.3); }
        /* ---- lights tab ---- */
        .strands { padding: 4px 16px; }
        .strand { padding: 14px 0; border-bottom: 1px solid var(--lss-line); display: flex; flex-wrap: wrap;
                  align-items: center; gap: 8px 12px; }
        .strand:last-child { border-bottom: none; }
        .strand .info { flex: 1 1 260px; min-width: 0; }
        .strand .name { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 15px; min-width: 0; flex-wrap: wrap; }
        .strand .name span.n { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .strand .strip { flex: 1 0 100%; }
        .meta { font-size: 13px; color: var(--lss-mut); display: flex; flex-wrap: wrap; gap: 2px 12px; margin-top: 2px; }
        .ip { font-family: var(--code-font-family, ui-monospace, monospace); color: inherit; font-size: 13px;
              text-decoration: underline dotted; padding: 2px 0; min-height: 24px; }
        .acts { display: flex; gap: 8px; flex: none; margin-left: auto; }
        .warn { flex: 1 0 100%; font-size: 13px; color: var(--lss-warn); }
        .serr { flex: 1 0 100%; font-size: 13px; color: var(--lss-err); font-weight: 500; }
        .strand[data-open] { cursor: pointer; }
        .strand[data-open]:hover .n { text-decoration: underline; }
        .xl { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; }
        .xl h2 { flex: 1 0 100%; }
        .xl .line { flex: 1 1 240px; min-width: 0; overflow-wrap: anywhere; font-size: 14px; font-family: var(--code-font-family, ui-monospace, monospace); }
        @media (max-width: 600px) {
          .strands { padding: 4px 12px; }
          .acts { margin-left: 0; flex: 1 0 100%; }
          .acts .btn { flex: 1; }
        }
        /* ---- form controls ---- */
        select, input[type=text], input[type=time], input[type=number], textarea {
          font: inherit; font-size: 14px; padding: 8px 10px; min-height: 44px; border: 1px solid var(--lss-line); border-radius: 10px;
          background: var(--lss-card); color: var(--lss-text); max-width: 100%; min-width: 0; transition: border-color .15s, box-shadow .15s; }
        select:focus, input:focus, textarea:focus { outline: none; border-color: var(--lss-acc); box-shadow: 0 0 0 3px var(--lss-tint); }
        textarea { width: 100%; min-height: 96px; font-family: var(--code-font-family, ui-monospace, monospace); font-size: 12.5px; resize: vertical; }
        input[type=range] { accent-color: var(--lss-acc); }
        .edit { flex: 1 0 100%; display: flex; flex-wrap: wrap; gap: 10px 12px; align-items: flex-end; }
        .edit label { display: flex; flex-direction: column; font-size: 12px; font-weight: 500; color: var(--lss-mut); gap: 4px; }
        .edit input { width: 120px; }
        .edit label.nm { flex: 1 1 180px; } .edit label.nm input { width: 100%; }
        .edit .free { flex: 1 0 100%; font-size: 12px; color: var(--lss-mut); }
        .edit .free button { color: var(--lss-acc); text-decoration: underline; font-size: 12px; min-height: 28px; padding: 0 4px; }
        /* ---- studio tab ---- */
        .st-top { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 12px; }
        .st-top label { flex: 1 1 240px; min-width: 0; display: flex; flex-direction: column; gap: 4px; font-size: 12px; font-weight: 500; color: var(--lss-mut); }
        .st-top select { width: 100%; }
        .st-top .sdot { display: none; }
        .st-top .st-strip { flex: 1 0 100%; height: 14px; border-radius: 7px; }
        .st-live { min-width: 0; }
        .st-live > * { display: block; margin: 0; }
        .st-ctl > * + *, .st-fx > * + * { margin-top: 16px; }
        .st-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; }
        .st-mode { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; justify-content: space-between; }
        .st-mode .ml { font-weight: 600; font-size: 15px; min-width: 0; overflow-wrap: anywhere; }
        .pw { display: inline-flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 600; padding: 8px 18px;
              min-height: 44px; border-radius: 999px; border: 1px solid var(--lss-line); background: var(--lss-soft); transition: background .15s, color .15s; }
        .pw[aria-checked=true] { background: var(--lss-acc); border-color: var(--lss-acc); color: var(--lss-onacc); box-shadow: 0 2px 10px var(--lss-tint); }
        .bri { flex: 1 1 200px; min-width: 0; display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--lss-mut); }
        .bri input { flex: 1; min-width: 0; height: 44px; }
        .bri .bv { width: 3.2em; text-align: right; color: var(--lss-text); font-weight: 600; }
        .sws { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
        .sw { width: 40px; height: 40px; border-radius: 50%; border: 3px solid transparent; box-shadow: inset 0 0 0 1px #0003; transition: transform .12s; }
        .sw:hover { transform: scale(1.08); }
        .sw.sel { border-color: var(--lss-card); box-shadow: 0 0 0 2px var(--lss-text), inset 0 0 0 1px #0003; }
        .st input[type=color] { width: 44px; height: 40px; padding: 0; border: 2px solid var(--lss-line); border-radius: 10px; background: none; cursor: pointer; }
        .grp { margin-top: 14px; }
        .grp h3 { font-size: 13px; font-weight: 600; margin: 0 0 8px; color: var(--lss-mut); letter-spacing: .02em; }
        .chips { display: flex; flex-wrap: wrap; gap: 8px; }
        .chip { font-size: 14px; padding: 6px 16px; min-height: 44px; border-radius: 999px; border: 1px solid var(--lss-line);
                background: var(--lss-card); max-width: 100%; overflow-wrap: anywhere; transition: background .15s, border-color .15s, box-shadow .15s, transform .1s; }
        .chip:hover { border-color: var(--lss-acc); }
        .chip:active { transform: scale(.97); }
        .chip[aria-pressed=true] { background: var(--lss-acc); border-color: var(--lss-acc); color: var(--lss-onacc); font-weight: 600; box-shadow: 0 2px 10px var(--lss-tint); }
        .chip.show { font-weight: 600; }
        .st-pat .chip { display: inline-flex; align-items: center; gap: 8px; }
        .st-pat .dots { display: inline-flex; align-items: center; flex: none; height: 14px; border-radius: 7px; overflow: hidden;
                        box-shadow: 0 0 0 1px #00000026; background: #0b0b10; }
        .st-pat .dots i { display: block; width: 8px; height: 14px; }
        .st-pat .dots.rb { width: 40px; }
        .st-pat .grp h3 .n { display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; margin-right: 8px;
                             border-radius: 50%; background: var(--lss-acc); color: var(--lss-onacc); font-size: 12px; }
        .st-pat .fxdesc { margin-top: 8px; font-size: 12.5px; color: var(--lss-mut); min-height: 1.3em; }
        .st-pat .spdrow { display: flex; align-items: center; gap: 10px; }
        .st-pat .spdrow input { flex: 1; min-width: 0; height: 44px; }
        .st-pat .spdrow span { flex: none; font-size: 13px; color: var(--lss-mut); }
        .st-pat .spdval { display: flex; justify-content: space-between; font-size: 12px; color: var(--lss-mut); }
        .st-pat .spdval .sv { font-weight: 600; color: var(--lss-text); }
        .st-pat .spd-off .spdrow { opacity: .55; }
        .ctl-off { opacity: .5; pointer-events: none; }
        .st-set .edit input { width: 120px; }
        .st-set .edit label.ip input { width: 150px; }
        .st-set .live { flex: 1 0 100%; font-size: 13px; color: var(--lss-mut); }
        .st-set .btnrow { flex: 1 0 100%; display: flex; gap: 8px; flex-wrap: wrap; }
        .st-set h2 { flex: 1 0 100%; margin: 0; }
        @media (max-width: 480px) {
          .edit label { flex: 1 1 130px; } .edit input, .st-set .edit input, .st-set .edit label.ip input { width: 100%; }
        }
        /* ---- phones: tabs become a bottom bar ---- */
        .narrow nav { position: fixed; left: 0; right: 0; bottom: 0; z-index: 8; margin: 0; height: auto;
                      background: var(--lss-card); color: var(--lss-mut); border-top: 1px solid var(--lss-line);
                      box-shadow: 0 -4px 16px rgba(0,0,0,.08); padding-bottom: env(safe-area-inset-bottom);
                      overflow-x: auto; scrollbar-width: none; }
        .narrow nav::-webkit-scrollbar { display: none; }
        .narrow nav button { flex: 1 0 56px; flex-direction: column; justify-content: center; gap: 3px; height: 62px; padding: 0 2px;
                             font-size: 11px; font-weight: 500; letter-spacing: 0; opacity: 1; min-width: 0; }
        .narrow nav button .ic { width: 52px; height: 28px; border-radius: 14px; align-items: center; justify-content: center; transition: background .2s, color .2s; }
        .narrow nav button:hover { background: none; }
        .narrow nav button[aria-selected=true] { color: var(--lss-text); font-weight: 700; box-shadow: none; }
        .narrow nav button[aria-selected=true] .ic { background: var(--lss-tint); color: var(--lss-acc); }
        .narrow main { padding: 14px 12px calc(88px + env(safe-area-inset-bottom)); }
        .narrow .box { padding: 14px; }
        .narrow .lhead h2 { font-size: 19px; }
        /* ---- playlists + schedule ---- */
        .pl { border: 1px solid var(--lss-line); border-radius: var(--r2); margin-top: 10px; overflow: hidden; background: var(--lss-card); }
        .plh { display: flex; align-items: center; gap: 8px; width: 100%; min-height: 52px; padding: 6px 14px; text-align: left; transition: background .15s; }
        .plh:hover { background: var(--lss-soft); }
        .plh .pn { flex: 1; min-width: 0; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .plh .chev { flex: none; transition: transform .18s var(--ease); color: var(--lss-mut); }
        .plh[aria-expanded=true] .chev { transform: rotate(180deg); }
        .ple { padding: 4px 14px 14px; border-top: 1px solid var(--lss-line); }
        .ple h3, .sed h3 { font-size: 13px; font-weight: 600; margin: 16px 0 6px; color: var(--lss-mut); }
        .row2 { display: flex; align-items: center; gap: 6px; min-height: 48px; border-bottom: 1px solid var(--lss-line); }
        .row2:last-child { border-bottom: none; }
        .row2 .rn { flex: 1; min-width: 0; overflow-wrap: anywhere; }
        .row2 .rn.gone { color: var(--lss-mut); font-style: italic; }
        .ib2 { width: 44px; height: 44px; border-radius: 50%; flex: none; display: inline-flex; align-items: center; justify-content: center; transition: background .15s; }
        .ib2:hover:not(:disabled) { background: var(--lss-soft2); }
        .ib2:disabled { opacity: .3; cursor: default; }
        .inl { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
        .inl input[type=text] { flex: 1 1 160px; min-width: 0; }
        .sbanner { display: flex; flex-direction: column; gap: 6px; border-left: 4px solid var(--lss-acc); }
        .sbanner .l1 { font-weight: 600; overflow-wrap: anywhere; }
        .sbanner .l2 { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; font-size: 14px; }
        .sbanner .stt { min-width: 0; overflow-wrap: anywhere; }
        .sbanner .sdot { display: inline-block; margin-right: 2px; }
        .sc { padding: 14px 16px; }
        .sc + .sc { margin-top: 12px; }
        .sc.off .sbody { opacity: .55; }
        .sc .top { display: flex; align-items: center; gap: 10px; }
        .sc .top .sn { flex: 1; min-width: 0; font-weight: 600; font-size: 16px; overflow-wrap: anywhere; }
        .tgl { position: relative; flex: none; width: 52px; height: 32px; border-radius: 16px; background: var(--disabled-text-color, #9e9e9e); transition: background .18s; }
        .tgl::after { content: ""; position: absolute; top: 4px; left: 4px; width: 24px; height: 24px; border-radius: 50%; background: #fff;
                      box-shadow: 0 1px 3px rgba(0,0,0,.3); transition: transform .18s var(--ease); }
        .tgl[aria-checked=true] { background: var(--lss-acc); }
        .tgl[aria-checked=true]::after { transform: translateX(20px); }
        .tgl:disabled { opacity: .5; cursor: default; }
        .tgl { margin: 6px 0; }
        .sbody { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; font-size: 14px; }
        .dd { display: flex; gap: 4px; flex-wrap: wrap; }
        .dd span { width: 28px; height: 28px; border-radius: 50%; font-size: 12px; font-weight: 600; display: inline-flex; align-items: center; justify-content: center;
                   border: 1px solid var(--lss-line); color: var(--lss-mut); }
        .dd span.on { background: var(--lss-acc); border-color: var(--lss-acc); color: var(--lss-onacc); }
        .sacts { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
        .sed { border-color: var(--lss-acc); }
        .sed form > * + * { margin-top: 16px; }
        .fld { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--lss-mut); min-width: 0; }
        .fld > span.lb, .sed h3.lb { font-size: 13px; font-weight: 600; color: var(--lss-mut); margin: 0; }
        .fld input[type=text], .fld select { width: 100%; }
        .seg { display: flex; border: 1px solid var(--lss-line); border-radius: var(--r2); overflow: hidden; background: var(--lss-soft); padding: 3px; gap: 3px; }
        .segb { flex: 1 1 0; min-height: 40px; padding: 4px 8px; font-size: 14px; font-weight: 500; border-radius: 9px; min-width: 0; transition: background .15s, color .15s; }
        .segb[aria-checked=true] { background: var(--lss-acc); color: var(--lss-onacc); font-weight: 600; box-shadow: 0 1px 4px rgba(0,0,0,.2); }
        .dsel { display: flex; gap: 6px; flex-wrap: wrap; }
        .dsel button { width: 44px; height: 44px; border-radius: 50%; border: 1px solid var(--lss-line); font-size: 14px; font-weight: 600; transition: background .15s; }
        .dsel button[aria-pressed=true] { background: var(--lss-acc); border-color: var(--lss-acc); color: var(--lss-onacc); }
        .edge-in { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 8px; }
        .edge-in .note { font-size: 12px; color: var(--lss-mut); }
        .chk { display: flex; align-items: center; gap: 10px; min-height: 44px; font-size: 14px; color: var(--lss-text); cursor: pointer; }
        .chk input { width: 22px; height: 22px; flex: none; accent-color: var(--lss-acc); margin: 0; }
        .seas { display: flex; flex-wrap: wrap; gap: 8px 16px; margin-top: 8px; }
        .seas .md { display: flex; align-items: center; gap: 6px; }
        .seas select { min-width: 76px; }
        .spk-l { max-height: 220px; overflow: auto; border: 1px solid var(--lss-line); border-radius: 10px; padding: 2px 10px; margin-top: 6px; }
        .vrow { display: flex; align-items: center; gap: 10px; margin-top: 4px; }
        .vrow input[type=range] { flex: 1; min-width: 0; height: 44px; }
        .vrow .bv { width: 3.2em; text-align: right; font-size: 14px; color: var(--lss-text); }
        .sed .btnrow { display: flex; gap: 8px; flex-wrap: wrap; }
        .notice { font-size: 13px; color: var(--lss-text); border-left: 4px solid var(--lss-warn); }
        details.spk > summary { cursor: pointer; min-height: 44px; display: flex; align-items: center; font-size: 14px; font-weight: 500; color: var(--lss-text); }
        /* ---- map tab ---- */
        .mp-chead { display: flex; align-items: center; justify-content: space-between; gap: 8px 12px; flex-wrap: wrap; margin-bottom: 12px; }
        .mp-chead h2 { margin: 0; }
        .mp-wrap { position: relative; width: 100%; border-radius: var(--r2); overflow: hidden; background: #0b0d12; aspect-ratio: 4 / 3; max-height: 560px;
                   box-shadow: inset 0 0 0 1px rgba(127,127,127,.3); }
        .mp-cv { position: absolute; inset: 0; width: 100%; height: 100%; touch-action: none; cursor: crosshair; display: block; }
        .mp-hint { margin-top: 10px; }
        .mp-legend { display: flex; flex-wrap: wrap; gap: 6px; }
        .mp-legend .chip { min-height: 36px; padding: 2px 12px; font-size: 13px; display: inline-flex; align-items: center; gap: 8px; }
        .narrow .mp-legend .chip { min-height: 40px; }
        .rsw { width: 12px; height: 12px; border-radius: 50%; flex: none; background: var(--c, #888); box-shadow: 0 0 6px var(--c, #888); }
        .zone, .runcard { border-width: 1px; transition: border-color .15s, box-shadow .15s; }
        .zone.sel, .runcard.sel { border-color: var(--lss-acc); box-shadow: 0 0 0 3px var(--lss-tint), var(--shadow); }
        .zhead { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
        .zhead input[type=text] { flex: 1 1 140px; font-weight: 600; font-size: 16px; }
        .zbody > * + * { margin-top: 16px; }
        .zr { display: flex; align-items: center; gap: 4px; min-height: 52px; padding: 2px 4px 2px 10px; border-radius: var(--r2);
              background: var(--lss-soft); }
        .zr + .zr { margin-top: 6px; }
        .zr .rsw { margin-right: 6px; }
        .zr .rt { flex: 1; min-width: 0; line-height: 1.25; }
        .zr .rt b { display: block; font-size: 14px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .zr .rt span { display: block; font-size: 12px; color: var(--lss-mut); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .zr .ib2[aria-pressed=true] { background: var(--lss-acc); color: var(--lss-onacc); }
        .zadd { display: flex; gap: 8px; margin-top: 8px; }
        .zadd select { flex: 1; }
        .axis { display: flex; flex-direction: column; gap: 8px; }
        .axis .vrow { margin: 0; }
        .axis .vrow .bv { width: 3.6em; font-weight: 600; }
        .zacts { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
        .zacts .btn { flex: 1 1 120px; }
        .fgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
        .fgrid label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; font-weight: 500; color: var(--lss-mut); min-width: 0; }
        .fgrid input, .fgrid select { width: 100%; }
        .fgrid .wide { grid-column: 1 / -1; }
        .rprob, .zprob { font-size: 13px; color: var(--lss-err); font-weight: 500; }
        .rprob:empty, .zprob:empty { display: none; }
        .runcard .rh { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
        .runcard .rh input { flex: 1; font-weight: 600; }
        .runcard details { margin-top: 4px; }
        .runcard summary { cursor: pointer; min-height: 44px; display: flex; align-items: center; font-size: 14px; font-weight: 500; color: var(--lss-acc); }
        .runcard details .btn { margin-top: 8px; }
        .mp-tools { display: flex; flex-wrap: wrap; gap: 8px; }
        .mp-banner { border-left: 4px solid var(--lss-err); }
        .mp-banner.info { border-left-color: var(--lss-acc); }
        .savebar { position: sticky; bottom: 0; z-index: 4; display: flex; align-items: center; gap: 8px 12px; flex-wrap: wrap;
                   padding: 10px 12px; border-radius: var(--r3); background: var(--lss-card); border: 1px solid var(--lss-acc);
                   box-shadow: 0 6px 24px rgba(0,0,0,.2); }
        .narrow .savebar { bottom: 4px; }
        .savebar .sbt { flex: 1 1 140px; font-weight: 600; font-size: 14px; }
        .savebar .serr { flex: 1 0 100%; order: -1; }
        .savebar .btn { flex: 0 1 auto; }
        .mp-body > * + *, .mp-zones > * + *, .mp-runs > * + * { margin-top: 16px; }
        .mp-zones > * + *, .mp-runs > * + * { margin-top: 12px; }
        .zone .st-pat { box-shadow: none; background: var(--lss-soft); border: none; padding: 14px; }
        .zone .st-pat h2 { font-size: 14px; margin-bottom: 4px; }
        .zone .zbody > .fld, .zone .zruns { min-width: 0; }
        .zruns h3 { margin: 0 0 8px; font-size: 13px; font-weight: 600; }
        .lhead .hsub.muted { flex: 1 0 100%; }
        .pls .lhead { margin-bottom: 4px; }
        .pls .lhead + .serr, .pls .skel { margin-top: 12px; }
        .box.empty .empty, .pls .empty { padding: 20px 8px; }
      </style>
      <div class="root">
        <header>
          <button class="menu" aria-label="Open the sidebar menu" hidden>${svg("M3 6h18v2H3zm0 5h18v2H3zm0 5h18v2H3z")}</button>
          <div class="brand">
            <span class="logo">${svg(PANEL_ICON.bulb, 24)}</span>
            <div class="bt"><h1>Light Shows</h1>
              <div class="hstat" role="status" aria-live="polite"><span class="hdot"></span><span class="htxt">Connecting\u2026</span></div></div>
          </div>
          <nav role="tablist" aria-label="Light Shows sections"></nav>
        </header>
        <main>
          <div class="box banner" role="region" aria-label="Getting started" hidden></div>
          <section id="p-play" role="tabpanel"></section>
          <section id="p-library" role="tabpanel">
            <div class="libcard"></div>
            <details class="box add">
              <summary><span class="hi">${svg(PANEL_ICON.upload)}</span>Add shows</summary>
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
            <div class="lhead"><h2><span class="hi">${svg(PANEL_ICON.clock)}</span>Schedules</h2><button class="btn sm add-sched" type="button" hidden>${svg(PANEL_ICON.add)}Add schedule</button></div>
            <div class="box sed" hidden></div>
            <div class="slist"></div>
          </section>
          <section id="p-lights" role="tabpanel"></section>
          <section id="p-map" role="tabpanel" class="mp"></section>
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
      b.innerHTML = `<span class="ic">${svg(path, 22)}</span><span>${label}</span>`;
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
      <div class="lhead"><h2><span class="hi">${svg(PANEL_ICON.bulb)}</span>Lights</h2><button class="btn alt sm add-light" type="button" hidden>${svg(PANEL_ICON.add)}Add a light</button></div>
      <div class="skel lights-skel" aria-busy="true" aria-label="Loading lights"><i></i><i></i><i></i></div>
      <div class="box strands" role="list" aria-label="Light strands" hidden></div>
      <div class="box empty no-lights" hidden><span class="eic">${svg(PANEL_ICON.bulb)}</span><div class="et">No lights yet</div>
        <div class="ed">Add a Wi-Fi light strip and it will show up here with a live preview.</div>
        <button class="btn" type="button">${svg(PANEL_ICON.add)}Add a light</button></div>
      <div class="box xl" hidden></div>`;
    const q = (c) => this._sections.lights.querySelector(c);
    this._strandBox = q(".strands");
    this._xlBox = q(".xl");
    this._addLight = q(".add-light");
    this._noLights = q(".no-lights");
    this._lightsSkel = q(".lights-skel");
    this._addLight.onclick = q(".no-lights .btn").onclick = () => this._navigate(INTEGRATION_PATH);
    this._buildStudio();
    this._buildMap();
    this._hstat = root.querySelector(".hstat");
    this._hstatTxt = root.querySelector(".htxt");
    this._hdot = root.querySelector(".hdot");
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
    if (id === "lights" || id === "studio" || id === "map") this._loadStrands();
    if (id === "studio") this._renderStudio();
    if (id !== "map") this._unsubscribeMap();
    if (id === "map") {
      this._subscribeMap();
      this._loadMap();
      requestAnimationFrame(() => this._drawMap());
    }
    this._syncWatch();
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
      if (this._tab === "lights" || this._tab === "studio" || this._tab === "map") this._loadStrands();
      this._syncWatch();
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
    this._syncWatch();
    if (this._tab === "map") {
      this._syncZones();
      this._drawMap();
    }
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
    this._lightsSkel.hidden = !!list || !!this._strandsError;
    this._noLights.hidden = !none;
    this._addLight.hidden = !list || none;
    if (!list || none) {
      box.hidden = true;
      this._xlBox.hidden = true;
      return;
    }
    box.hidden = false;
    box.textContent = "";
    this._stripEls?.clear();
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
    if (s.entity_id && s.loaded) row.append(this._strip(s.entity_id));
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
    const h2 = this._el("h2");
    h2.innerHTML = `<span class="hi">${svg(PANEL_ICON.link)}</span>`;
    h2.append("For xLights / EZPlayer");
    box.append(h2, this._el("div", "line", line), copy);
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
      <div class="skel st-skel" aria-busy="true" aria-label="Loading"><i></i><i></i><i></i></div>
      <div class="box empty st-empty" hidden></div>
      <div class="st-body" hidden>
        <div class="box st-top">
          <label>Light<select class="pick" aria-label="Choose a light"></select></label>
          <span class="sdot" role="img"></span><span class="state"></span>
          <button class="btn alt sm idb" type="button">Identify</button>
          <canvas class="strip st-strip" aria-hidden="true"></canvas>
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
        <div class="pat-slot"></div>
        <div class="box st-fx"><h2><span class="hi">${svg(PANEL_ICON.palette)}</span>Effects</h2><div class="groups"></div></div>
        <form class="box st-set edit" novalidate>
          <h2><span class="hi">${svg(PANEL_ICON.gear)}</span>Settings</h2>
          <label class="nm">Name<input class="f-nm" type="text" required maxlength="100"></label>
          <label class="ip">IP address<input class="f-ip" type="text" inputmode="decimal" autocomplete="off" required></label>
          <label>Start pixel<input class="f-st" type="number" step="1" required></label>
          <label>Bulbs<input class="f-bl" type="number" step="1" required></label>
          <div class="live" aria-live="polite"></div>
          <div class="free">Next free start pixel: <button type="button" class="use"></button></div>
          <div class="warns"></div>
          <div class="serr" role="alert" hidden></div>
          <div class="btnrow"><button class="btn sm save" type="submit">${svg(PANEL_ICON.save)}Save</button>
            <button class="btn alt sm reset" type="button">Reset</button>
            <button class="btn alt sm rm" type="button">Remove light</button></div>
          <div class="free note">Changing the IP address reloads the light.</div>
        </form>
        <div class="serr st-err" role="alert" hidden></div>
      </div>`;
    const patBox = this._patternBox({
      title: "Pattern", speedText: "Speed: for any effect on this light",
      onPick: (key, n) => this._pattern({ [key]: n }), onSpeed: (v) => this._pattern({ speed: v }),
    });
    sec.querySelector(".pat-slot").replaceWith(patBox);
    const q = (c) => sec.querySelector(c);
    this._st = {
      skel: q(".st-skel"), strip: q(".st-strip"), empty: q(".st-empty"), body: q(".st-body"), pick: q(".pick"), dot: q(".sdot"), state: q(".state"),
      idb: q(".idb"), live: q(".st-live"), ml: q(".ml"), back: q(".back"), pw: q(".pw"), brs: q(".brs"), bv: q(".bv"),
      sws: q(".sws"), groups: q(".groups"), fx: q(".st-fx"), ctl: q(".st-ctl"), form: q(".st-set"), err: q(".st-err"),
      nm: q(".f-nm"), ip: q(".f-ip"), st: q(".f-st"), bl: q(".f-bl"), liveTxt: q(".st-set .live"),
      use: q(".use"), warns: q(".warns"), ferr: q(".st-set .serr"), save: q(".save"), reset: q(".reset"),
      rm: q(".rm"), pat: patBox,
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

  _dots(name) {
    const [cols, width] = PATTERN_COLOURS[name] || [null, 1];
    const d = this._el("span", "dots");
    d.setAttribute("aria-hidden", "true");
    if (cols && !cols.length) {
      d.classList.add("rb");
      d.style.background = RAINBOW_CSS;
    } else if (cols) {
      for (const c of cols) for (let k = 0; k < width; k++) {
        const i = document.createElement("i");
        i.style.background = `rgb(${c.join(",")})`;
        d.append(i);
      }
    } else d.style.display = "none";
    return d;
  }

  async _pattern(data) {
    const s = this._currentStrand();
    if (!s?.entity_id || !this._hass) return;
    try {
      await this._hass.callService("light_show_studio", "set_pattern", data, { entity_id: s.entity_id });
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
    st.skel.hidden = !!list || !!this._strandsError;
    st.empty.hidden = !(none || (!list && this._strandsError));
    st.body.hidden = !list || none;
    if (!st.empty.hidden) {
      if (none) {
        this._emptyState(st.empty, PANEL_ICON.bulb, "No lights yet", "Add a light to control it from the Studio.", "Add a light", () => this._navigate(INTEGRATION_PATH));
      } else this._emptyState(st.empty, PANEL_ICON.close, "Couldn't load the lights", this._strandsError);
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
    st.strip.dataset.e = s.entity_id || "";
    st.strip.hidden = !s.entity_id;
    this._stripEls.add(st.strip);
    this._syncWatch();
    // live card for this light only (one subscription at a time)
    if (s.entity_id) {
      if (!this._studioCards.has(s.entity_id))
        this._studioCards.set(s.entity_id, this._card("light-show-studio-card", { entity: s.entity_id, size: 1.7 }));
      const card = this._studioCards.get(s.entity_id);
      if (st.live.firstChild !== card) st.live.replaceChildren(card);
      this._feed(card);
    } else {
      const e = this._el("div", "box empty");
      this._emptyState(e, PANEL_ICON.bulb, "This light isn't loaded", "There is nothing to show until it comes online.");
      st.live.replaceChildren(e);
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
    // pattern + pattern effect + speed (shared with the Map tab)
    st.pat.classList.toggle("ctl-off", !usable);
    const pats = Array.isArray(a.pattern_list) ? a.pattern_list : [];
    const pfx = Array.isArray(a.pattern_effect_list) ? a.pattern_effect_list : [];
    st.pat.hidden = !pats.length && !st.pat._r.sig; // older integration without patterns
    this._patternFill(st.pat, pats, pfx);
    const patOn = on && effect === "Pattern";
    // speed applies to the pattern and to every other moving effect; only a still colour or the show ignores it
    const moving = on && effect && effect !== SHOW && !(patOn && STILL.has(a.pattern_effect));
    this._patternSync(st.pat, { pattern: patOn ? a.pattern : null, effect: patOn ? a.pattern_effect : null, speed: a.speed, moving });
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
      group("More effects", list.filter((n) => n !== SHOW && n !== "Pattern" && !SEASONAL.test(n)));
      if (!list.length) st.groups.append(this._el("div", "muted", "Effects show up when the light is online."));
    }
    st.groups.querySelectorAll(".chip").forEach((b) => b.setAttribute("aria-pressed", String(on && effect === b.dataset.fx)));
    if (s.entity_id) this._feed(this._studioCards.get(s.entity_id));
  }

  // --- small shared pieces: empty states, live status, live strips, pattern controls ---------
  _emptyState(el, icon, title, text, btnLabel, fn) {
    el.textContent = "";
    el.classList.add("empty");
    const ic = this._el("span", "eic");
    ic.innerHTML = svg(icon);
    el.append(ic, this._el("div", "et", title));
    if (text) el.append(this._el("div", "ed", text));
    if (btnLabel) el.append(this._btn(btnLabel, "btn", fn));
  }

  _head(icon, title) {
    const h = this._el("h2");
    h.innerHTML = `<span class="hi">${svg(icon)}</span>`;
    h.append(title);
    return h;
  }

  // header status: what is playing and how many lights are on
  _renderHeader() {
    const el = this._hstat;
    if (!el || !this._hass) return;
    const states = this._hass.states;
    const player = this._findPlayer();
    const ids = this._lights();
    const on = ids.filter((id) => states[id].state === "on");
    const zones = [...new Set(on.map((id) => states[id].attributes.zone).filter(Boolean))];
    const parts = [];
    let cls = on.length ? "on" : "";
    if (player && (player.state === "playing" || player.state === "paused")) {
      const t = player.attributes.media_title;
      parts.push(`${player.state === "playing" ? "Playing" : "Paused"}${t ? " " + t : ""}`);
      if (player.state === "playing") cls = "run";
    }
    if (zones.length) {
      parts.push(`Zone ${zones.join(", ")}`);
      cls = "run";
    }
    parts.push(ids.length ? `${on.length} of ${ids.length} light${ids.length === 1 ? "" : "s"} on` : "No lights yet");
    const text = parts.join(" · ");
    if (text !== this._hText) {
      this._hText = text;
      this._hstatTxt.textContent = text;
      el.title = text;
    }
    this._hdot.className = "hdot " + cls;
  }

  // a canvas that shows a light's live colours as a strip
  _strip(entity) {
    const c = document.createElement("canvas");
    c.className = "strip";
    c.dataset.e = entity;
    c.setAttribute("aria-hidden", "true");
    this._stripEls.add(c);
    requestAnimationFrame(() => this._paintStrip(c));
    this._syncWatch();
    return c;
  }

  _wantedFrames() {
    const strands = this._strands || [];
    if (this._tab === "lights") return strands.map((s) => s.entity_id).filter(Boolean);
    if (this._tab === "studio") {
      const s = this._currentStrand();
      return s?.entity_id ? [s.entity_id] : [];
    }
    if (this._tab === "map") return this._mapEntities();
    return [];
  }

  _syncWatch() {
    if (!this._hass || !this.isConnected) return;
    this._watchFrames(this._wantedFrames());
  }

  _watchFrames(ids) {
    const want = new Set(ids.slice(0, 16));
    for (const [id, u] of this._subs) {
      if (want.has(id)) continue;
      this._subs.delete(id);
      try {
        if (typeof u === "function") u();
      } catch (e) {
        /* connection already closed */
      }
    }
    for (const id of want) if (!this._subs.has(id)) this._subscribeFrames(id);
  }

  async _subscribeFrames(id) {
    if (!this._hass?.connection) return;
    const token = {};
    this._subs.set(id, token);
    try {
      const unsub = await this._hass.connection.subscribeMessage((m) => this._onFrames(id, m), {
        type: "light_show_studio/subscribe",
        entity_id: id,
      });
      if (this._subs.get(id) === token && this.isConnected) this._subs.set(id, unsub);
      else unsub(); // not wanted any more, or gone, while we waited
    } catch (err) {
      if (this._subs.get(id) === token) this._subs.delete(id); // tried again on the next sync
    }
  }

  _onFrames(id, m) {
    this._frames[id] = m;
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => {
      this._raf = 0;
      this._drawStrips();
      if (this._tab === "map") this._drawMap();
    });
  }

  _drawStrips() {
    for (const c of this._stripEls) {
      if (!c.isConnected) this._stripEls.delete(c);
      else if (c.offsetParent !== null) this._paintStrip(c);
    }
  }

  _paintStrip(cv) {
    const w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
      cv.width = Math.round(w * dpr);
      cv.height = Math.round(h * dpr);
    }
    const g = cv.getContext("2d");
    g.clearRect(0, 0, cv.width, cv.height);
    const m = this._frames[cv.dataset.e];
    if (!m || !m.on || !m.bulbs) return;
    const n = m.bulbs, W = cv.width, H = cv.height;
    if (n <= W) {
      const bw = W / n;
      for (let i = 0; i < n; i++) {
        g.fillStyle = "#" + m.frame.substr(i * 6, 6);
        g.fillRect(Math.floor(i * bw), 0, Math.ceil(bw) + 0.5, H);
      }
    } else {
      for (let x = 0; x < W; x++) {
        g.fillStyle = "#" + m.frame.substr(Math.floor((x / W) * n) * 6, 6);
        g.fillRect(x, 0, 1, H);
      }
    }
  }

  // --- pattern -> effect -> speed controls, used by the Studio tab and by every zone on the Map tab ---
  _patternBox({ title, speedText, onPick, onSpeed }) {
    const box = this._el("div", "box st-pat");
    box.innerHTML = `<h2><span class="hi">${svg(PANEL_ICON.palette)}</span>${title}</h2>
      <div class="grp"><h3><span class="n" aria-hidden="true">1</span>Pattern: choose the colours</h3>
        <div class="chips pat-chips" role="group" aria-label="Pattern"></div></div>
      <div class="grp"><h3><span class="n" aria-hidden="true">2</span>Effect: choose the motion</h3>
        <div class="chips pfx-chips" role="group" aria-label="Pattern effect"></div>
        <div class="fxdesc" aria-live="polite"></div></div>
      <div class="grp spdgrp"><h3><span class="n" aria-hidden="true">3</span>${speedText}</h3>
        <div class="spdrow"><span>Slow</span><input class="sps" type="range" min="1" max="100" step="1" aria-label="Effect speed, slow to fast">
          <span>Fast</span></div>
        <div class="spdval"><span>Normal is 50</span><span>Speed <b class="sv"></b></span></div></div>`;
    const q = (c) => box.querySelector(c);
    const r = { sig: "", onPick, patChips: q(".pat-chips"), pfxChips: q(".pfx-chips"), sps: q(".sps"), sv: q(".sv"), fxd: q(".fxdesc"), spdg: q(".spdgrp") };
    r.sps.oninput = () => {
      r.sv.textContent = r.sps.value;
      r.sps.setAttribute("aria-valuetext", `${r.sps.value} of 100`);
      clearTimeout(r.t);
      r.t = setTimeout(() => onSpeed(Number(r.sps.value)), 200);
    };
    r.sps.onchange = () => {
      clearTimeout(r.t);
      onSpeed(Number(r.sps.value));
    };
    box._r = r;
    return box;
  }

  // (re)build the chips when the lists change
  _patternFill(box, pats, pfx) {
    const r = box._r;
    const sig = pats.join("|") + "#" + pfx.join("|");
    if (sig === r.sig || !pats.length) return;
    r.sig = sig;
    const fill = (row, names, key) => {
      row.textContent = "";
      for (const n of names) {
        const b = this._el("button", "chip");
        b.type = "button";
        b.dataset.v = n;
        if (key === "pattern") b.append(this._dots(n));
        b.append(this._el("span", "", n));
        if (key === "effect" && EFFECT_INFO[n]) {
          b.title = EFFECT_INFO[n];
          b.setAttribute("aria-label", `${n}: ${EFFECT_INFO[n]}`);
        } else if (key === "pattern") b.setAttribute("aria-label", `${n} pattern`);
        b.onclick = () => {
          row.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c === b)));
          if (key === "effect") r.fxd.textContent = EFFECT_INFO[n] || "";
          r.onPick(key, n);
        };
        row.append(b);
      }
    };
    fill(r.patChips, pats, "pattern");
    fill(r.pfxChips, pfx, "effect");
  }

  // show the current pattern, effect and speed; moving = false greys the speed slider
  _patternSync(box, { pattern, effect, speed, moving }) {
    const r = box._r;
    r.patChips.querySelectorAll(".chip").forEach((b) => b.setAttribute("aria-pressed", String(!!pattern && pattern === b.dataset.v)));
    r.pfxChips.querySelectorAll(".chip").forEach((b) => b.setAttribute("aria-pressed", String(!!effect && effect === b.dataset.v)));
    r.fxd.textContent = effect && EFFECT_INFO[effect] ? EFFECT_INFO[effect] : "";
    if (this._root.getRootNode().activeElement !== r.sps && speed != null) r.sps.value = speed;
    r.sv.textContent = r.sps.value;
    r.sps.setAttribute("aria-valuetext", `${r.sps.value} of 100`);
    r.spdg.classList.toggle("spd-off", !moving);
    r.sps.title = moving ? "" : "Speed applies once an effect is running";
  }

  // --- map tab -------------------------------------------------------------------
  // Zones run one pattern across several runs (a run = a range of pixels on one light). Written to the
  // websocket contract in docs/MAPPING_PLAN.md: map/get, map/set, map/zone/start|stop|speed.
  static _clamp01(v) {
    return Math.min(1, Math.max(0, v));
  }

  static _runPos(run) {
    const n = Math.max(0, Math.floor(run.count) || 0), pts = run.pts || [];
    if (!n || !pts.length) return [];
    if (pts.length === n) return pts;
    if (pts.length === 1) return Array.from({ length: n }, () => pts[0]);
    const cum = [0];
    for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    const total = cum[cum.length - 1] || 1e-9;
    const out = new Array(n);
    let k = 1;
    for (let i = 0; i < n; i++) {
      const d = n > 1 ? (i / (n - 1)) * total : total / 2;
      while (k < cum.length - 1 && cum[k] < d) k++;
      const span = cum[k] - cum[k - 1] || 1e-9;
      const t = Math.min(1, Math.max(0, (d - cum[k - 1]) / span));
      out[i] = [pts[k - 1][0] + (pts[k][0] - pts[k - 1][0]) * t, pts[k - 1][1] + (pts[k][1] - pts[k - 1][1]) * t];
    }
    return out;
  }

  _runColour(id) {
    const i = Math.max(0, (this._map?.runs || []).findIndex((r) => r.id === id));
    return `hsl(${(i * 57 + 200) % 360} 85% 62%)`;
  }

  _lanePts(i) {
    const y = 0.12 + 0.76 * ((i % 8) / 7);
    return [[0.12, y], [0.88, y]];
  }

  _newId(prefix, list) {
    let n = list.length + 1;
    const ids = new Set(list.map((x) => x.id));
    while (ids.has(prefix + n)) n++;
    return prefix + n;
  }

  _normMap(m) {
    const num = (v, d) => (Number.isFinite(Number(v)) && v !== null && v !== "" ? Number(v) : d);
    const runs = (m?.runs || []).map((r, i) => {
      let pts = Array.isArray(r.pts) ? r.pts.filter((p) => Array.isArray(p) && p.length >= 2).map((p) => [Number(p[0]), Number(p[1])]) : [];
      if (!pts.length) pts = this._lanePts(i);
      else if (pts.length === 1) pts = [pts[0], [Math.min(1, pts[0][0] + 0.2), pts[0][1]]];
      return { ...r, id: String(r.id), entry_id: r.entry_id, name: r.name || `Run ${i + 1}`, first: num(r.first, 0), count: num(r.count, 1), reverse: !!r.reverse, pts };
    });
    const ids = new Set(runs.map((r) => r.id));
    const zones = (m?.zones || []).map((z, i) => ({
      ...z, id: String(z.id), name: z.name || `Zone ${i + 1}`, runs: (z.runs || []).filter((id) => ids.has(id)),
      mode: z.mode === "spatial" ? "spatial" : "string",
      axis: z.axis && Array.isArray(z.axis.centre) ? { centre: [num(z.axis.centre[0], 0.5), num(z.axis.centre[1], 0.5)] } : { angle: num(z.axis?.angle, 0) },
      pattern: z.pattern || "Classic Multicolor", effect: z.effect || "Chase", speed: Math.min(100, Math.max(1, Math.round(num(z.speed, 50)))),
    }));
    return { ...m, version: m?.version || 1, runs, zones, backdrop: { kind: "none", w: 1, h: 1, ...(m?.backdrop || {}) } };
  }

  _mapDirty() {
    return !!this._map && JSON.stringify(this._map) !== this._mapSaved;
  }

  _mLight(entry) {
    return (this._mapLights || []).find((l) => l.entry_id === entry) || null;
  }

  _mEntity(entry) {
    return (this._strands || []).find((s) => s.entry_id === entry)?.entity_id || null;
  }

  _mapEntities() {
    if (!this._map) return [];
    return [...new Set(this._map.runs.map((r) => this._mEntity(r.entry_id)).filter(Boolean))];
  }

  _mapErrText(err, fallback) {
    if (err?.code === "unknown_command" || /unknown command/i.test(String(err?.message || err))) {
      return "Mapping isn't available yet. Update Light Show Studio and restart Home Assistant.";
    }
    return this._err(err, fallback);
  }

  _buildMap() {
    const sec = this._sections.map;
    sec.innerHTML = `
      <div class="lhead"><h2><span class="hi">${svg(PANEL_ICON.map)}</span>Map</h2>
        <span class="hsub">Run one pattern across several lights, or parts of a light, as one thing.</span></div>
      <div class="skel mp-skel" aria-busy="true" aria-label="Loading the map"><i></i><i></i><i></i></div>
      <div class="box mp-banner mp-note" role="status" hidden></div>
      <div class="mp-body" hidden>
        <div class="box mp-canvasbox">
          <div class="mp-chead"><h2><span class="hi">${svg(PANEL_ICON.target)}</span>Layout</h2><div class="mp-legend" role="group" aria-label="Runs"></div></div>
          <div class="mp-wrap"><canvas class="mp-cv" role="img" aria-label="Layout of the runs. Drag the round handles to place each run."></canvas></div>
          <div class="muted mp-hint" aria-live="polite"></div>
        </div>
        <div class="lhead"><h2><span class="hi">${svg(PANEL_ICON.layers)}</span>Zones</h2>
          <button class="btn alt sm add-zone" type="button">${svg(PANEL_ICON.add)}New zone</button></div>
        <div class="mp-zones" role="list" aria-label="Zones"></div>
        <div class="lhead"><h2><span class="hi">${svg(PANEL_ICON.route)}</span>Runs</h2>
          <div class="mp-tools"><button class="btn alt sm kitchen" type="button" hidden>Kitchen preset</button>
            <button class="btn alt sm add-run" type="button">${svg(PANEL_ICON.add)}Add a run</button></div></div>
        <div class="mp-runs" role="list" aria-label="Runs"></div>
        <div class="savebar" role="region" aria-label="Unsaved map changes" hidden>
          <div class="serr" role="alert" hidden></div>
          <span class="sbt">Unsaved changes</span>
          <button class="btn alt sm discard" type="button">Discard</button>
          <button class="btn sm save-map" type="button">${svg(PANEL_ICON.save)}Save map</button>
        </div>
      </div>`;
    const q = (c) => sec.querySelector(c);
    const p = (this._mp = {
      skel: q(".mp-skel"), note: q(".mp-note"), body: q(".mp-body"), cv: q(".mp-cv"), wrap: q(".mp-wrap"), hint: q(".mp-hint"),
      legend: q(".mp-legend"), zones: q(".mp-zones"), runs: q(".mp-runs"), kitchen: q(".kitchen"), addZone: q(".add-zone"),
      addRun: q(".add-run"), bar: q(".savebar"), barErr: q(".savebar .serr"), save: q(".save-map"), discard: q(".discard"),
    });
    p.addZone.onclick = () => this._addZone();
    p.addRun.onclick = () => this._addRun();
    p.kitchen.onclick = () => this._kitchenPreset();
    p.save.onclick = () => this._mapSave();
    p.discard.onclick = () => {
      this._map = JSON.parse(this._mapSaved);
      this._mapErr = "";
      this._pickCentre = null;
      this._renderMap();
    };
    p.cv.onpointerdown = (e) => this._mapDown(e);
    p.cv.onpointermove = (e) => this._mapMove(e);
    p.cv.onpointerup = p.cv.onpointercancel = (e) => this._mapUp(e);
    this._zoneRefs = new Map();
    this._runRefs = new Map();
    if (typeof ResizeObserver === "function") new ResizeObserver(() => this._drawMap()).observe(p.wrap);
  }

  async _loadMap(force) {
    if (!this._hass || this._mapLoading) return;
    if (this._map && this._mapDirty() && !force) return;
    this._mapLoading = true;
    try {
      const r = await this._hass.callWS({ type: "light_show_studio/map/get" });
      this._mapOld = false;
      this._mapLoadErr = "";
      this._mapLights = Array.isArray(r?.lights) ? r.lights : [];
      this._map = this._normMap(r?.map);
      this._mapSaved = JSON.stringify(this._map);
      if (Array.isArray(r?.running)) this._mapRunning = new Set(r.running);
    } catch (err) {
      if (/unknown/i.test(this._mapErrText(err, "")) || err?.code === "unknown_command") this._mapOld = true;
      else this._mapLoadErr = this._mapErrText(err, "Couldn't load the map.");
    }
    this._mapLoading = false;
    this._renderMap();
  }

  _renderMap() {
    const p = this._mp;
    if (!p) return;
    const m = this._map;
    p.skel.hidden = !!m || this._mapOld || !!this._mapLoadErr;
    const note = p.note;
    note.hidden = !(this._mapOld || (this._mapLoadErr && !m));
    if (!note.hidden) {
      note.textContent = "";
      note.classList.toggle("info", !!this._mapOld);
      if (this._mapOld) {
        note.append(this._el("strong", "", "Mapping isn't available yet"), this._el("div", "muted", "Update Light Show Studio to the latest version and restart Home Assistant, then reopen this tab."));
      } else {
        note.append(this._el("div", "serr", this._mapLoadErr), this._btn("Try again", "btn alt sm", () => this._loadMap(true)));
      }
    }
    p.body.hidden = !m;
    if (!m) return;
    const sel = this._mapSel;
    if (sel.zone && !m.zones.some((z) => z.id === sel.zone)) sel.zone = null;
    if (sel.run && !m.runs.some((r) => r.id === sel.run)) sel.run = null;
    const bd = m.backdrop;
    p.wrap.style.aspectRatio = bd.w !== bd.h ? `${Math.min(2.5, Math.max(0.5, bd.w / bd.h))}` : "4 / 3";
    p.kitchen.hidden = !(this._isAdmin() && (this._mapLights || []).some((l) => l.kind === "wled" && l.bulbs === 904));
    p.addRun.hidden = p.addZone.hidden = !this._isAdmin();
    this._renderLegend();
    this._renderMapZones();
    this._renderMapRuns();
    this._renderSaveBar();
    this._refreshProblems();
    this._applySel();
    this._syncWatch();
  }

  _renderLegend() {
    const p = this._mp;
    p.legend.textContent = "";
    for (const r of this._map.runs) {
      const b = this._btn("", "chip");
      b.dataset.r = r.id;
      b.setAttribute("aria-pressed", String(this._mapSel.run === r.id));
      const sw = this._el("span", "rsw");
      sw.style.setProperty("--c", this._runColour(r.id));
      b.append(sw, this._el("span", "", r.name || "Run"));
      b.onclick = () => this._selectRun(this._mapSel.run === r.id ? null : r.id);
      p.legend.append(b);
    }
  }

  _applySel() {
    const sel = this._mapSel;
    const p = this._mp;
    p.zones.querySelectorAll(".zone").forEach((c) => c.classList.toggle("sel", c.dataset.z === sel.zone));
    p.runs.querySelectorAll(".runcard").forEach((c) => c.classList.toggle("sel", c.dataset.r === sel.run));
    p.legend.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.r === sel.run)));
    const z = this._map.zones.find((x) => x.id === sel.zone);
    const picking = this._pickCentre && this._pickCentre === sel.zone;
    p.hint.textContent = picking
      ? "Tap the layout to set the centre of the effect."
      : "Drag the round handles to place each run. Lit dots show what the lights are doing right now." +
        (this._map.runs.some((r) => r.pts.length > 2) ? " A run with imported positions moves as a whole." : "") +
        (z && z.mode === "spatial" ? " The white guide shows the direction of the selected zone." : "");
    this._drawMap();
  }

  _selectRun(id) {
    this._mapSel.run = id;
    this._applySel();
  }

  _selectZone(id) {
    if (this._mapSel.zone === id && !this._mapSel.run) return;
    this._mapSel.zone = id;
    this._mapSel.run = null;
    if (this._pickCentre && this._pickCentre !== id) this._pickCentre = null;
    this._applySel();
  }

  // --- canvas ------------------------------------------------------------------------
  _drawMap() {
    const p = this._mp, m = this._map;
    if (!p || !m || p.body.hidden || this._tab !== "map") return;
    const W = p.wrap.clientWidth, H = p.wrap.clientHeight;
    if (!W || !H) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const cv = p.cv;
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
      cv.width = Math.round(W * dpr);
      cv.height = Math.round(H * dpr);
    }
    const g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    g.lineWidth = 1;
    g.strokeStyle = "rgba(255,255,255,.06)";
    g.beginPath();
    for (let i = 1; i < 10; i++) {
      g.moveTo(Math.round((i * W) / 10) + 0.5, 0);
      g.lineTo(Math.round((i * W) / 10) + 0.5, H);
      g.moveTo(0, Math.round((i * H) / 10) + 0.5);
      g.lineTo(W, Math.round((i * H) / 10) + 0.5);
    }
    g.stroke();
    const sel = this._mapSel;
    const zone = m.zones.find((z) => z.id === sel.zone);
    const inZone = new Set(zone?.runs || []);
    const handles = [];
    const X = (x) => x * W, Y = (y) => y * H;
    if (!m.runs.length) {
      g.fillStyle = "rgba(255,255,255,.5)";
      g.font = "600 14px system-ui, sans-serif";
      g.textAlign = "center";
      g.fillText("No runs yet", W / 2, H / 2);
    }
    for (const run of m.runs) {
      const col = this._runColour(run.id);
      const focus = !zone || inZone.has(run.id) || sel.run === run.id;
      const alpha = focus ? 1 : 0.35;
      const pts = run.pts;
      g.globalAlpha = 0.5 * alpha;
      g.strokeStyle = col;
      g.lineWidth = sel.run === run.id ? 3 : 1.5;
      g.beginPath();
      pts.forEach((pt, i) => (i ? g.lineTo(X(pt[0]), Y(pt[1])) : g.moveTo(X(pt[0]), Y(pt[1]))));
      g.stroke();
      const pos = LightShowStudioPanel._runPos(run);
      const ent = this._mEntity(run.entry_id);
      const fr = ent && this._frames[ent]?.on ? this._frames[ent].frame : null;
      const rad = pos.length > 500 ? 1.7 : pos.length > 150 ? 2.5 : 3.5;
      for (let i = 0; i < pos.length; i++) {
        let c = null;
        if (fr) {
          const hex = fr.substr((run.first + i) * 6, 6);
          if (hex.length === 6 && hex !== "000000") c = "#" + hex;
        }
        g.globalAlpha = c ? alpha : alpha * 0.3;
        g.fillStyle = c || col;
        g.beginPath();
        g.arc(X(pos[i][0]), Y(pos[i][1]), c ? rad * 1.25 : rad, 0, 6.2832);
        g.fill();
      }
      g.globalAlpha = alpha;
      const ends = pts.length > 1 ? [0, pts.length - 1] : [0];
      for (const idx of ends) handles.push({ kind: "end", run, idx, x: X(pts[idx][0]), y: Y(pts[idx][1]), col, alpha });
    }
    g.globalAlpha = 1;
    // direction / centre guide for the selected zone
    if (zone && zone.mode === "spatial") {
      g.strokeStyle = g.fillStyle = "rgba(255,255,255,.85)";
      g.lineWidth = 2;
      g.setLineDash([6, 5]);
      if (zone.axis.centre) {
        const cx = X(zone.axis.centre[0]), cy = Y(zone.axis.centre[1]);
        for (const f of [0.12, 0.24, 0.36]) {
          g.globalAlpha = 0.35;
          g.beginPath();
          g.arc(cx, cy, f * Math.min(W, H) * 1.4, 0, 6.2832);
          g.stroke();
        }
        g.globalAlpha = 1;
        handles.push({ kind: "centre", zone, x: cx, y: cy, col: "#fff", alpha: 1 });
      } else {
        const a = ((zone.axis.angle || 0) * Math.PI) / 180;
        const dx = Math.cos(a), dy = -Math.sin(a), L = Math.min(W, H) * 0.3;
        const cx = W / 2, cy = H / 2;
        g.beginPath();
        g.moveTo(cx - dx * L, cy - dy * L);
        g.lineTo(cx + dx * L, cy + dy * L);
        g.stroke();
        g.setLineDash([]);
        g.beginPath();
        const hx = cx + dx * L, hy = cy + dy * L;
        g.moveTo(hx, hy);
        g.lineTo(hx - dx * 12 - dy * 7, hy - dy * 12 + dx * 7);
        g.lineTo(hx - dx * 12 + dy * 7, hy - dy * 12 - dx * 7);
        g.closePath();
        g.fill();
      }
      g.setLineDash([]);
    }
    // handles on top
    g.font = "600 12px system-ui, sans-serif";
    g.textAlign = "left";
    for (const h of handles) {
      const big = h.kind === "centre" || sel.run === h.run?.id;
      g.globalAlpha = h.alpha;
      g.beginPath();
      g.arc(h.x, h.y, big ? 11 : 9, 0, 6.2832);
      g.fillStyle = "#0b0d12";
      g.fill();
      g.lineWidth = 3;
      g.strokeStyle = h.col;
      g.stroke();
      if (big) {
        g.beginPath();
        g.arc(h.x, h.y, 3.5, 0, 6.2832);
        g.fillStyle = h.col;
        g.fill();
      }
      if (h.kind === "end" && h.idx === 0 && h.run.name) {
        const lx = Math.min(h.x + 14, W - 8 - g.measureText(h.run.name).width), ly = Math.max(14, h.y - 14);
        g.fillStyle = "rgba(0,0,0,.55)";
        g.fillText(h.run.name, lx + 1, ly + 1);
        g.fillStyle = "#fff";
        g.fillText(h.run.name, Math.max(6, lx), ly);
      }
    }
    g.globalAlpha = 1;
    this._mapHandles = handles;
  }

  _mapXY(e) {
    const r = this._mp.cv.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top, r.width, r.height];
  }

  _mapHit(x, y, touch) {
    let best = null, bd = touch ? 30 : 18;
    for (const h of this._mapHandles || []) {
      const d = Math.hypot(h.x - x, h.y - y);
      if (d <= bd) {
        bd = d;
        best = h;
      }
    }
    return best;
  }

  _mapDown(e) {
    if (!this._map) return;
    const [x, y, w, h] = this._mapXY(e);
    const hit = this._mapHit(x, y, e.pointerType === "touch");
    const admin = this._isAdmin();
    if (hit && admin) {
      if (hit.kind === "end") this._mapSel.run = hit.run.id;
      this._drag = { h: hit, sx: x / w, sy: y / h, orig: hit.run ? hit.run.pts.map((pt) => [...pt]) : null };
      try {
        this._mp.cv.setPointerCapture(e.pointerId);
      } catch (err) {
        /* synthetic pointer */
      }
      this._applySel();
      e.preventDefault();
      return;
    }
    const zone = this._map.zones.find((z) => z.id === this._mapSel.zone);
    if (admin && this._pickCentre && zone && this._pickCentre === zone.id) {
      zone.axis = { centre: [LightShowStudioPanel._clamp01(x / w), LightShowStudioPanel._clamp01(y / h)] };
      this._pickCentre = null;
      this._mapChanged();
      this._renderMapZones();
      this._applySel();
      return;
    }
    // tap near a dot: select that run
    let near = null, nd = 16;
    for (const run of this._map.runs) {
      const pos = LightShowStudioPanel._runPos(run);
      const step = Math.max(1, Math.floor(pos.length / 400));
      for (let i = 0; i < pos.length; i += step) {
        const d = Math.hypot(pos[i][0] * w - x, pos[i][1] * h - y);
        if (d < nd) {
          nd = d;
          near = run;
        }
      }
    }
    this._selectRun(near ? near.id : null);
  }

  _mapMove(e) {
    if (!this._map) return;
    const [x, y, w, h] = this._mapXY(e);
    const d = this._drag;
    if (!d) {
      this._mp.cv.style.cursor = this._mapHit(x, y, false) && this._isAdmin() ? "grab" : this._pickCentre ? "crosshair" : "default";
      return;
    }
    const c = LightShowStudioPanel._clamp01;
    const nx = c(x / w), ny = c(y / h);
    if (d.h.kind === "centre") d.h.zone.axis = { centre: [nx, ny] };
    else {
      const run = d.h.run;
      if (run.pts.length <= 2) run.pts[d.h.idx] = [nx, ny];
      else {
        const dx = nx - d.sx, dy = ny - d.sy;
        run.pts = d.orig.map((pt) => [c(pt[0] + dx), c(pt[1] + dy)]);
      }
    }
    // keep the handle under the finger even before the next full draw
    d.h.x = nx * w;
    d.h.y = ny * h;
    if (!this._mapRaf) {
      this._mapRaf = requestAnimationFrame(() => {
        this._mapRaf = 0;
        this._drawMap();
      });
    }
  }

  _mapUp(e) {
    if (!this._drag) return;
    this._drag = null;
    try {
      this._mp.cv.releasePointerCapture(e.pointerId);
    } catch (err) {
      /* already released */
    }
    this._mapChanged();
    this._renderMapZones(); // the centre readout
  }

  // --- change tracking + validation ----------------------------------------------------------
  _mapChanged() {
    this._mapErr = "";
    this._renderSaveBar();
    this._refreshProblems();
    this._drawMap();
  }

  _mapProblems() {
    const out = { runs: {}, zones: {}, any: false };
    const add = (k, id, msg) => {
      (out[k][id] = out[k][id] || []).push(msg);
      out.any = true;
    };
    const m = this._map;
    m.runs.forEach((r, i) => {
      const L = this._mLight(r.entry_id);
      if (!String(r.name).trim()) add("runs", r.id, "Give the run a name.");
      if (!L) return add("runs", r.id, "Pick a light.");
      const okFirst = Number.isInteger(r.first) && r.first >= 0, okCount = Number.isInteger(r.count) && r.count >= 1;
      if (!okFirst) add("runs", r.id, "First pixel must be 0 or more.");
      if (!okCount) add("runs", r.id, "Count must be 1 or more.");
      if (okFirst && okCount) {
        if (r.first + r.count > L.bulbs) add("runs", r.id, `${L.title} has ${L.bulbs} pixels (0–${L.bulbs - 1}), so pixels ${r.first}–${r.first + r.count - 1} don't fit.`);
        for (const o of m.runs.slice(i + 1)) {
          if (o.entry_id !== r.entry_id || !Number.isInteger(o.first) || !Number.isInteger(o.count) || o.count < 1) continue;
          if (Math.max(r.first, o.first) <= Math.min(r.first + r.count, o.first + o.count) - 1) {
            add("runs", r.id, `Overlaps ${o.name}.`);
            add("runs", o.id, `Overlaps ${r.name}.`);
          }
        }
      }
    });
    const seen = new Set();
    for (const z of m.zones) {
      if (!String(z.name).trim()) add("zones", z.id, "Give the zone a name.");
      else if (seen.has(z.name.trim().toLowerCase())) add("zones", z.id, "Another zone already has this name.");
      seen.add(String(z.name).trim().toLowerCase());
      if (!z.runs.length) add("zones", z.id, "Add at least one run.");
    }
    return out;
  }

  _refreshProblems() {
    if (!this._map || !this._mp) return;
    const pr = this._mapProblems();
    for (const [id, ref] of this._runRefs) {
      const run = this._map.runs.find((r) => r.id === id);
      ref.prob.textContent = (pr.runs[id] || []).join(" ");
      const L = run && this._mLight(run.entry_id);
      ref.info.textContent = L && Number.isInteger(run.first) && Number.isInteger(run.count) && run.count > 0
        ? `Pixels ${run.first}–${run.first + run.count - 1} of ${L.bulbs}`
        : "";
    }
    for (const [id, ref] of this._zoneRefs) ref.prob.textContent = (pr.zones[id] || []).join(" ");
  }

  _renderSaveBar() {
    const p = this._mp;
    if (!p || !this._map) return;
    const dirty = this._mapDirty();
    p.bar.hidden = !(dirty || this._mapErr);
    p.barErr.textContent = this._mapErr || "";
    p.barErr.hidden = !this._mapErr;
    p.save.disabled = !this._isAdmin();
    p.bar.querySelector(".sbt").textContent = dirty ? "Unsaved changes" : "Not saved";
  }

  async _mapSave() {
    if (!this._map) return false;
    const pr = this._mapProblems();
    if (pr.any) {
      this._mapErr = "Fix the problems marked below before saving.";
      this._renderSaveBar();
      this._refreshProblems();
      return false;
    }
    const btn = this._mp.save;
    btn.disabled = true;
    try {
      const r = await this._hass.callWS({ type: "light_show_studio/map/set", map: this._map });
      this._mapErr = "";
      this._map = this._normMap(r?.map || this._map);
      this._mapSaved = JSON.stringify(this._map);
      this._renderMap();
      return true;
    } catch (err) {
      this._mapErr = this._mapErrText(err, "Couldn't save the map.");
      this._renderSaveBar();
      return false;
    } finally {
      btn.disabled = !this._isAdmin();
    }
  }

  // --- zones ----------------------------------------------------------------------------------
  _patternLists() {
    for (const id of this._lights()) {
      const a = this._hass.states[id]?.attributes || {};
      if (Array.isArray(a.pattern_list) && a.pattern_list.length) return [a.pattern_list, a.pattern_effect_list || []];
    }
    return [Object.keys(PATTERN_COLOURS), Object.keys(EFFECT_INFO).filter((n) => n !== "Steady").concat("Steady")];
  }

  // running state: pushed by map/subscribe; the lights' "zone" attribute is the fallback
  async _subscribeMap() {
    if (this._mapUnsub || this._mapSubbing || !this._hass?.connection || !this.isConnected) return;
    this._mapSubbing = true;
    try {
      const unsub = await this._hass.connection.subscribeMessage((m) => {
        this._mapRunning = new Set(Array.isArray(m?.running) ? m.running : []);
        this._syncZones();
      }, { type: "light_show_studio/map/subscribe" });
      if (this._tab === "map" && this.isConnected) this._mapUnsub = unsub;
      else unsub();
    } catch (err) {
      /* older backend: the zone attribute on the lights is used instead */
    }
    this._mapSubbing = false;
  }

  _unsubscribeMap() {
    try {
      this._mapUnsub?.();
    } catch (e) {
      /* connection already closed */
    }
    this._mapUnsub = null;
  }

  _zoneRunning(zone) {
    if (!this._hass) return false;
    if (this._mapRunning) return this._mapRunning.has(zone.id);
    return zone.runs.some((id) => {
      const run = this._map.runs.find((r) => r.id === id);
      const ent = run && this._mEntity(run.entry_id);
      return ent && this._hass.states[ent]?.attributes.zone === zone.name;
    });
  }

  _iconBtn(icon, label, onclick, extra) {
    const b = this._el("button", "ib2");
    b.type = "button";
    b.innerHTML = svg(icon, 22);
    b.title = label;
    b.setAttribute("aria-label", label);
    b.onclick = onclick;
    if (extra) Object.assign(b.dataset, extra);
    return b;
  }

  _renderMapZones() {
    const box = this._mp.zones;
    box.textContent = "";
    this._zoneRefs = new Map();
    const admin = this._isAdmin();
    if (!this._map.zones.length) {
      const e = this._el("div", "box empty");
      this._emptyState(e, PANEL_ICON.layers, "No zones yet",
        "A zone plays one pattern across one or more runs, in step, as if they were a single string.",
        admin ? "New zone" : "", () => this._addZone());
      box.append(e);
      return;
    }
    for (const z of this._map.zones) box.append(this._zoneCard(z, admin));
    this._restoreFocus();
    this._syncZones();
  }

  _zoneCard(zone, admin) {
    const card = this._el("div", "box zone");
    card.setAttribute("role", "listitem");
    card.dataset.z = zone.id;
    const select = () => this._selectZone(zone.id);
    card.addEventListener("pointerdown", (e) => {
      if (!e.target.closest("button")) select();
    });
    const head = this._el("div", "zhead");
    const name = this._el("input");
    name.type = "text";
    name.maxLength = 60;
    name.value = zone.name;
    name.dataset.fk = `zn-${zone.id}`;
    name.setAttribute("aria-label", "Zone name");
    name.oninput = () => {
      zone.name = name.value;
      this._mapChanged();
    };
    name.onfocus = select;
    const pill = this._el("span", "pill", "Idle");
    head.append(name, pill, this._iconBtn(PANEL_ICON.trash, `Delete ${zone.name}`, () => this._deleteZone(zone)));
    const body = this._el("div", "zbody");
    // runs, in order
    const runsBox = this._el("div", "zruns");
    runsBox.append(this._el("h3", "lb muted", "Runs, in order (the pattern flows from top to bottom)"));
    zone.runs.forEach((id, i) => {
      const run = this._map.runs.find((r) => r.id === id);
      if (!run) return;
      const L = this._mLight(run.entry_id);
      const row = this._el("div", "zr");
      row.setAttribute("role", "listitem");
      const sw = this._el("span", "rsw");
      sw.style.setProperty("--c", this._runColour(run.id));
      const t = this._el("div", "rt");
      t.append(this._el("b", "", run.name), this._el("span", "", `${L ? L.title : "Unknown light"} · ${run.count} pixel${run.count === 1 ? "" : "s"}${L && L.segmented === false ? " · average colour only" : ""}`));
      const key = (s) => ({ fk: `zr-${zone.id}-${id}-${s}` });
      const move = (j) => {
        const ids = [...zone.runs];
        [ids[i], ids[j]] = [ids[j], ids[i]];
        zone.runs = ids;
        this._keepFocus(`zr-${zone.id}-${id}-${j < i ? "up" : "dn"}`);
        this._mapChanged();
        this._renderMapZones();
      };
      const up = this._iconBtn(PANEL_ICON.up, `Move ${run.name} up`, () => move(i - 1), key("up"));
      const dn = this._iconBtn(PANEL_ICON.down, `Move ${run.name} down`, () => move(i + 1), key("dn"));
      up.disabled = i === 0;
      dn.disabled = i === zone.runs.length - 1;
      const rev = this._iconBtn(PANEL_ICON.swap, `Reverse ${run.name}`, () => {
        run.reverse = !run.reverse;
        this._keepFocus(`zr-${zone.id}-${id}-rev`);
        this._mapChanged();
        this._renderMapZones();
        this._renderMapRuns();
      }, key("rev"));
      rev.setAttribute("aria-pressed", String(!!run.reverse));
      rev.title = run.reverse ? `${run.name} runs backwards (tap to undo)` : `Reverse ${run.name}`;
      const rm = this._iconBtn(PANEL_ICON.close, `Take ${run.name} out of ${zone.name}`, () => {
        zone.runs = zone.runs.filter((x) => x !== id);
        this._mapChanged();
        this._renderMapZones();
      }, key("rm"));
      row.append(sw, t, up, dn, rev, rm);
      runsBox.append(row);
    });
    const free = this._map.runs.filter((r) => !zone.runs.includes(r.id));
    if (free.length) {
      const add = this._el("div", "zadd");
      const sel = this._el("select");
      sel.setAttribute("aria-label", `Add a run to ${zone.name}`);
      for (const r of free) sel.append(Object.assign(this._el("option", "", `${r.name} — ${this._mLight(r.entry_id)?.title || "?"}`), { value: r.id }));
      add.append(sel, this._btn("Add run", "btn alt sm", () => {
        zone.runs = [...zone.runs, sel.value];
        this._mapChanged();
        this._renderMapZones();
      }));
      runsBox.append(add);
    } else if (!this._map.runs.length) {
      runsBox.append(this._el("div", "muted", "Add a run below first."));
    }
    // mode
    const mode = this._el("div", "fld");
    mode.append(this._el("span", "lb", "How the pattern moves"));
    mode.append(this._seg([["string", "Along the string"], ["spatial", "By position"]], zone.mode, "How the pattern moves", (v) => {
      zone.mode = v;
      this._mapChanged();
      this._renderMapZones();
      this._applySel();
    }));
    mode.append(this._el("div", "muted", zone.mode === "string"
      ? "The runs are joined end to end in the order above and the pattern travels along them."
      : "The pattern sweeps across the layout, so lights that are next to each other in the room light up together."));
    body.append(runsBox, mode);
    if (zone.mode === "spatial") {
      const ax = this._el("div", "axis fld");
      const useCentre = !!zone.axis.centre;
      ax.append(this._seg([["angle", "A direction"], ["centre", "From a point"]], useCentre ? "centre" : "angle", "Spatial axis", (v) => {
        zone.axis = v === "centre" ? { centre: [0.5, 0.5] } : { angle: 0 };
        this._pickCentre = null;
        this._mapChanged();
        this._renderMapZones();
        this._applySel();
      }));
      if (useCentre) {
        const [cx, cy] = zone.axis.centre;
        const picking = this._pickCentre === zone.id;
        const row = this._el("div", "inl");
        row.append(this._btn(picking ? "Tap the layout…" : "Tap the layout to set the centre", picking ? "btn sm" : "btn alt sm", () => {
          this._pickCentre = picking ? null : zone.id;
          this._selectZone(zone.id);
          this._renderMapZones();
          this._applySel();
        }), this._el("span", "muted", `Centre at ${Math.round(cx * 100)}%, ${Math.round(cy * 100)}%. You can also drag the white target.`));
        ax.append(row);
      } else {
        const row = this._el("div", "vrow");
        const r = this._el("input");
        r.type = "range";
        r.min = 0;
        r.max = 359;
        r.value = zone.axis.angle || 0;
        r.setAttribute("aria-label", "Direction in degrees");
        const bv = this._el("span", "bv", `${Math.round(zone.axis.angle || 0)}°`);
        r.oninput = () => {
          zone.axis = { angle: Number(r.value) };
          bv.textContent = `${r.value}°`;
          this._selectZone(zone.id);
          this._mapChanged();
        };
        row.append(r, bv);
        ax.append(row, this._el("div", "muted", "0° sweeps left to right, 90° bottom to top, 180° right to left."));
      }
      body.append(ax);
    }
    // pattern -> effect -> speed (same controls as the Studio tab)
    const pb = this._patternBox({
      title: "Pattern", speedText: "Speed: how fast the zone moves",
      onPick: (key, n) => {
        zone[key] = n;
        this._mapChanged();
        this._syncZones();
        if (this._zoneRunning(zone)) {
          clearTimeout(this._zT);
          this._zT = setTimeout(() => this._zoneCall("start", zone.id), 250);
        }
      },
      onSpeed: (v) => {
        zone.speed = v;
        this._mapChanged();
        if (this._zoneRunning(zone)) this._zoneCall("speed", zone.id);
      },
    });
    const [pats, pfx] = this._patternLists();
    this._patternFill(pb, pats, pfx);
    this._patternSync(pb, { pattern: zone.pattern, effect: zone.effect, speed: zone.speed, moving: !STILL.has(zone.effect) });
    body.append(pb);
    const prob = this._el("div", "zprob");
    prob.setAttribute("role", "alert");
    const err = this._el("div", "serr", "");
    err.setAttribute("role", "alert");
    err.hidden = true;
    const acts = this._el("div", "zacts");
    const start = this._btn("", "btn", () => this._zoneCall("start", zone.id));
    const stop = this._btn("", "btn alt", () => this._zoneCall("stop", zone.id));
    stop.innerHTML = `${svg(PANEL_ICON.stop)}Stop`;
    acts.append(start, stop);
    body.append(prob, err, acts);
    card.append(head, body);
    this._zoneRefs.set(zone.id, { card, pill, start, stop, err, prob });
    if (!admin) card.querySelectorAll("input,select,textarea,button").forEach((el) => (el.disabled = true));
    return card;
  }

  // running state follows the lights' "zone" attribute
  _syncZones() {
    if (!this._map || !this._zoneRefs) return;
    for (const [id, ref] of this._zoneRefs) {
      const zone = this._map.zones.find((z) => z.id === id);
      if (!zone) continue;
      const running = this._zoneRunning(zone);
      ref.pill.className = "pill " + (running ? "ok run" : "");
      ref.pill.textContent = running ? "Running" : "Idle";
      ref.card.classList.toggle("running", running);
      if (!ref.busy) ref.start.innerHTML = `${svg(PANEL_ICON.play)}${running ? "Restart" : "Start"}`;
      ref.stop.disabled = !running || !this._isAdmin();
      ref.start.disabled = ref.busy || !this._isAdmin();
    }
  }

  async _zoneCall(what, id) {
    const ref = this._zoneRefs.get(id);
    const zone = this._map?.zones.find((z) => z.id === id);
    if (!ref || !zone) return;
    ref.err.hidden = true;
    ref.busy = true;
    ref.start.disabled = true;
    try {
      if (what === "start" && this._mapDirty() && !(await this._mapSave())) {
        const r2 = this._zoneRefs.get(id);
        if (r2) {
          r2.err.textContent = this._mapErr || "Save the map first.";
          r2.err.hidden = false;
        }
        return;
      }
      const z = this._map.zones.find((x) => x.id === id) || zone;
      const msg = { type: `light_show_studio/map/zone/${what}`, zone_id: id };
      if (what === "start") Object.assign(msg, { pattern: z.pattern, effect: z.effect, speed: z.speed });
      if (what === "speed") msg.speed = z.speed;
      await this._hass.callWS(msg);
    } catch (err) {
      const r2 = this._zoneRefs.get(id) || ref;
      r2.err.textContent = this._mapErrText(err, "That didn't work.");
      r2.err.hidden = false;
    } finally {
      const r2 = this._zoneRefs.get(id) || ref;
      r2.busy = false;
      ref.busy = false;
      this._syncZones();
    }
  }

  _addZone() {
    const m = this._map;
    if (!m) return;
    const [pats] = this._patternLists();
    const first = m.runs.find((r) => r.id === this._mapSel.run) || m.runs[0];
    let n = m.zones.length + 1;
    while (m.zones.some((z) => z.name === `Zone ${n}`)) n++;
    const zone = {
      id: this._newId("z", m.zones), name: `Zone ${n}`, runs: first ? [first.id] : [], mode: "string", axis: { angle: 0 },
      pattern: pats.includes("Classic Multicolor") ? "Classic Multicolor" : pats[0], effect: "Chase", speed: 50,
    };
    m.zones.push(zone);
    this._mapSel = { zone: zone.id, run: null };
    this._keepFocus(`zn-${zone.id}`);
    this._mapChanged();
    this._renderMapZones();
    this._applySel();
    this._mp.zones.querySelector(`[data-z="${zone.id}"]`)?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }

  _deleteZone(zone) {
    if (!window.confirm(`Delete the zone "${zone.name}"? The runs stay.`)) return;
    this._map.zones = this._map.zones.filter((z) => z.id !== zone.id);
    if (this._mapSel.zone === zone.id) this._mapSel.zone = null;
    this._mapChanged();
    this._renderMapZones();
    this._applySel();
  }

  // --- runs ---------------------------------------------------------------------------------------
  _renderMapRuns() {
    const box = this._mp.runs;
    box.textContent = "";
    this._runRefs = new Map();
    const admin = this._isAdmin();
    if (!this._map.runs.length) {
      const e = this._el("div", "box empty");
      this._emptyState(e, PANEL_ICON.route, "No runs yet",
        "A run is a stretch of pixels on one light. Add one for each part you want to place on the layout.",
        admin ? "Add a run" : "", () => this._addRun());
      box.append(e);
      return;
    }
    for (const r of this._map.runs) box.append(this._runCard(r, admin));
    this._restoreFocus();
    this._refreshProblems();
  }

  _runCard(run, admin) {
    const card = this._el("div", "box runcard");
    card.setAttribute("role", "listitem");
    card.dataset.r = run.id;
    card.addEventListener("pointerdown", (e) => {
      if (!e.target.closest("button") && this._mapSel.run !== run.id) this._selectRun(run.id);
    });
    const rh = this._el("div", "rh");
    const sw = this._el("span", "rsw");
    sw.style.setProperty("--c", this._runColour(run.id));
    const name = this._el("input");
    name.type = "text";
    name.maxLength = 60;
    name.value = run.name;
    name.dataset.fk = `rn-${run.id}`;
    name.setAttribute("aria-label", "Run name");
    name.oninput = () => {
      run.name = name.value;
      this._mapChanged();
    };
    name.onchange = () => {
      this._renderLegend();
      this._renderMapZones();
      this._applySel();
    };
    rh.append(sw, name, this._iconBtn(PANEL_ICON.trash, `Remove ${run.name}`, () => this._removeRun(run)));
    const grid = this._el("div", "fgrid");
    const lab = (text, input, cls) => {
      const l = this._el("label", cls || "", text);
      l.append(input);
      return l;
    };
    const light = this._el("select");
    for (const L of this._mapLights || []) light.append(Object.assign(this._el("option", "", `${L.title} (${L.bulbs} px)`), { value: L.entry_id }));
    if (!this._mLight(run.entry_id)) light.append(Object.assign(this._el("option", "", "(light removed)"), { value: run.entry_id || "" }));
    light.value = run.entry_id || "";
    light.onchange = () => {
      run.entry_id = light.value;
      this._mapChanged();
      this._renderMapZones();
      this._renderMapRuns();
      this._syncWatch();
    };
    const num = (key, min) => {
      const i = this._el("input");
      i.type = "number";
      i.step = 1;
      i.min = min;
      i.inputMode = "numeric";
      i.value = Number.isFinite(run[key]) ? run[key] : "";
      i.oninput = () => {
        run[key] = i.value === "" ? NaN : Number(i.value);
        this._mapChanged();
      };
      i.onchange = () => {
        this._renderMapZones();
      };
      return i;
    };
    grid.append(lab("Light", light, "wide"), lab("First pixel (0 is the first)", num("first", 0)), lab("How many pixels", num("count", 1)));
    const info = this._el("div", "muted");
    const rev = this._el("label", "chk");
    const rcb = this._el("input");
    rcb.type = "checkbox";
    rcb.checked = !!run.reverse;
    rcb.onchange = () => {
      run.reverse = rcb.checked;
      this._mapChanged();
      this._renderMapZones();
    };
    rev.append(rcb, document.createTextNode("Runs backwards (last pixel first)"));
    const L = this._mLight(run.entry_id);
    const flags = this._el("div", "inl");
    if (L && L.loaded === false) flags.append(this._el("span", "pill err", "Not loaded"));
    if (L && L.segmented === false) flags.append(this._el("span", "pill warn", "Shows one average colour"));
    if (run.pts.length > 2) flags.append(this._el("span", "pill acc", `${run.pts.length} imported positions`));
    const prob = this._el("div", "rprob");
    prob.setAttribute("role", "alert");
    // import positions
    const det = this._el("details");
    det.append(this._el("summary", "", "Import positions"));
    const ta = this._el("textarea");
    ta.placeholder = '{"w": 1999, "h": 1500, "points": [[x, y], ...]}';
    ta.setAttribute("aria-label", "Positions as JSON");
    ta.spellcheck = false;
    const ierr = this._el("div", "serr");
    ierr.hidden = true;
    const row = this._el("div", "inl");
    row.append(this._btn("Use these positions", "btn alt sm", () => this._importPositions(run, ta.value, ierr)));
    if (run.pts.length > 2) row.append(this._btn("Back to a straight line", "btn alt sm", () => {
      run.pts = [run.pts[0], run.pts[run.pts.length - 1]];
      this._mapChanged();
      this._renderMapRuns();
      this._applySel();
    }));
    det.append(this._el("div", "muted", "Paste the points from the bulb placer. There must be one point for every pixel in this run."), ta, ierr, row);
    if (L && L.layout) det.append(this._el("div", "muted", "This light reports its own layout, which is used automatically when a run has no positions."));
    card.append(rh, grid, info, rev, flags, prob, det);
    this._runRefs.set(run.id, { card, info, prob });
    if (!admin) card.querySelectorAll("input,select,textarea,button").forEach((el) => (el.disabled = true));
    return card;
  }

  _addRun() {
    const m = this._map;
    if (!m) return;
    const cur = m.runs.find((r) => r.id === this._mapSel.run);
    const L = this._mLight(cur?.entry_id) || (this._mapLights || [])[0];
    if (!L) {
      this._mapErr = "Add a light first, then come back to map it.";
      this._renderSaveBar();
      return;
    }
    const used = m.runs.filter((r) => r.entry_id === L.entry_id);
    const first = used.reduce((a, r) => Math.max(a, (r.first || 0) + (r.count || 0)), 0);
    const run = {
      id: this._newId("r", m.runs), entry_id: L.entry_id, name: `Run ${m.runs.length + 1}`, first,
      count: Math.max(1, Math.min(30, L.bulbs - first)), reverse: false, pts: this._lanePts(m.runs.length),
    };
    m.runs.push(run);
    this._mapSel.run = run.id;
    this._keepFocus(`rn-${run.id}`);
    this._mapChanged();
    this._renderLegend();
    this._renderMapZones();
    this._renderMapRuns();
    this._applySel();
    this._syncWatch();
    this._mp.runs.querySelector(`[data-r="${run.id}"]`)?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }

  _removeRun(run) {
    const m = this._map;
    m.runs = m.runs.filter((r) => r.id !== run.id);
    for (const z of m.zones) z.runs = z.runs.filter((id) => id !== run.id);
    if (this._mapSel.run === run.id) this._mapSel.run = null;
    this._mapChanged();
    this._renderLegend();
    this._renderMapZones();
    this._renderMapRuns();
    this._applySel();
    this._syncWatch();
  }

  // the 904-pixel WLED kitchen: toe-kick, island, counter, above the cabinets
  _kitchenPreset() {
    const L = (this._mapLights || []).find((l) => l.kind === "wled" && l.bulbs === 904);
    const m = this._map;
    if (!L || !m) return;
    const old = new Set(m.runs.filter((r) => r.entry_id === L.entry_id).map((r) => r.id));
    if (old.size && !window.confirm(`Replace the ${old.size} existing run${old.size === 1 ? "" : "s"} on ${L.title} with the four kitchen runs?`)) return;
    m.runs = m.runs.filter((r) => !old.has(r.id));
    for (const z of m.zones) z.runs = z.runs.filter((id) => !old.has(id));
    const spec = [
      ["Toe-kick", 0, 279, [[0.1, 0.9], [0.9, 0.9]]],
      ["Island", 279, 145, [[0.3, 0.62], [0.7, 0.62]]],
      ["Counter", 424, 192, [[0.1, 0.38], [0.9, 0.38]]],
      ["Above cabinets", 616, 288, [[0.1, 0.12], [0.9, 0.12]]],
    ];
    const ids = [];
    for (const [name, first, count, pts] of spec) {
      const run = { id: this._newId("r", m.runs), entry_id: L.entry_id, name, first, count, reverse: false, pts };
      m.runs.push(run);
      ids.push(run.id);
    }
    if (!m.zones.some((z) => z.name === "Kitchen")) {
      const [pats] = this._patternLists();
      m.zones.push({
        id: this._newId("z", m.zones), name: "Kitchen", runs: ids, mode: "string", axis: { angle: 0 },
        pattern: pats.includes("Classic Multicolor") ? "Classic Multicolor" : pats[0], effect: "Chase", speed: 50,
      });
    } else {
      const kz = m.zones.find((z) => z.name === "Kitchen");
      kz.runs = [...kz.runs, ...ids];
    }
    this._mapSel = { zone: m.zones.find((z) => z.name === "Kitchen").id, run: null };
    this._mapChanged();
    this._renderMap();
  }

  _importPositions(run, text, errEl) {
    const fail = (t) => {
      errEl.textContent = t;
      errEl.hidden = false;
    };
    errEl.hidden = true;
    let data;
    try {
      data = JSON.parse(text);
    } catch (e) {
      return fail("That isn't valid JSON. Paste it exactly as the bulb placer exported it.");
    }
    const pts = Array.isArray(data) ? data : data?.points;
    if (!Array.isArray(pts) || !pts.length) return fail('Expected {"w": 1999, "h": 1500, "points": [[x, y], ...]}.');
    const xy = pts.map((p) => (Array.isArray(p) ? [Number(p[0]), Number(p[1])] : [Number(p?.x), Number(p?.y)]));
    if (xy.some((p) => !Number.isFinite(p[0]) || !Number.isFinite(p[1]))) return fail("Every point needs a number for x and for y.");
    const w = Number(data.w) || 0, h = Number(data.h) || 0;
    let norm;
    if (w > 0 && h > 0) norm = xy.map((p) => [p[0] / w, p[1] / h]);
    else if (xy.every((p) => p[0] >= 0 && p[0] <= 1 && p[1] >= 0 && p[1] <= 1)) norm = xy;
    else return fail('Include "w" and "h", the size of the area the points were placed in.');
    if (norm.length !== run.count) return fail(`That has ${norm.length} points but this run has ${run.count} pixels. Change the pixel count, or paste matching positions.`);
    const c = LightShowStudioPanel._clamp01;
    run.pts = norm.map((p) => [Math.round(c(p[0]) * 10000) / 10000, Math.round(c(p[1]) * 10000) / 10000]);
    const bd = this._map.backdrop;
    if (w > 0 && h > 0 && bd.w === 1 && bd.h === 1) this._map.backdrop = { ...bd, w, h };
    this._mapChanged();
    this._renderMap();
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
    head.append(this._head(PANEL_ICON.queue, "Playlists"));
    if (admin && this._pl) {
      const nb = this._btn("New playlist", "btn alt sm", () => {
        this._plCreating = true;
        this._renderPlaylists();
        this._root.querySelector(".pl-new input")?.focus();
      });
      head.append(nb);
    }
    box.append(head);
    head.append(this._el("span", "hsub muted", "A named, ordered list of shows (Halloween, Christmas). Pick one on the Play tab or in a schedule."));
    if (this._plError) box.append(this._el("div", "serr", this._plError));
    if (!this._pl) {
      const sk = this._el("div", "skel");
      sk.setAttribute("aria-busy", "true");
      sk.append(document.createElement("i"), document.createElement("i"));
      box.append(sk);
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
      const e = this._el("div", "empty");
      this._emptyState(e, PANEL_ICON.queue, "No playlists yet", admin ? "Group shows into a playlist to play them in order or on a schedule." : "", admin ? "New playlist" : "", () => {
        this._plCreating = true;
        this._renderPlaylists();
        this._root.querySelector(".pl-new input")?.focus();
      });
      box.append(e);
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
      const e = this._el("div", "box empty");
      this._emptyState(e, PANEL_ICON.clock, "No schedules yet", "Start a playlist automatically at sunset, or at a time you choose, on the days you pick.",
        admin ? "Add schedule" : "", () => this._openEditor());
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
    ed.append(this._head(PANEL_ICON.clock, d.id ? "Edit schedule" : "New schedule"));
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
      for (const n of names) fx.append(Object.assign(this._el("option", "", n === "Pattern" ? "Pattern (as last set in Studio)" : n), { value: n }));
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
    if (this._tab === "map") this._syncZones();
    this._renderHeader();
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
    head.append(this._head(PANEL_ICON.bulb, "Let's get set up"));
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
