// Light Show Studio: the "Scan" wizard of the Map tab (camera mapping, like the Twinkly app).
// pick a light -> aim the phone -> the lights flash a code while the camera watches -> positions appear in 3D -> fix outliers
// -> optional side scan for depth -> save. The maths is in light-show-studio-scan.js, the 3D view in light-show-studio-map3d.js.
// Two methods: "point" (visible LEDs, binary code) and "glow" (hidden strips: a lit stretch is stepped along each run and the
// centre of the glow it makes is found; the pixels between are interpolated). Glow is recommended for WLED lights, and offered
// when a point scan finds few lights.
// "Scan from several angles" (point method): the same colour scan from 3-6 places; each view's decoded points are kept and light-show-studio-
// multiview.js solves the cameras and every light's 3D position from them (see docs/MAPPING_V2.md, Multi-angle scan).
// Whatever happens (cancel, error, leaving the tab) map/scan/stop is sent so the light goes back to what it showed.

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const MAX_W = 1920;

const CSS = `
.scw { position: fixed; inset: 0; z-index: 30; display: flex; flex-direction: column; background: var(--lss-bg); color: var(--lss-text); }
.scw-h { display: flex; align-items: center; gap: 4px; min-height: 60px; padding: 0 6px 0 16px; flex: none; background: var(--app-header-background-color, var(--lss-acc));
         color: var(--app-header-text-color, var(--lss-onacc)); box-shadow: 0 2px 8px rgba(0,0,0,.18); }
.scw-h h2 { margin: 0; font-size: 18px; font-weight: 600; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.scw-h .ib2:hover:not(:disabled) { background: rgba(255,255,255,.18); }
.scw-steps { display: flex; gap: 6px; padding: 10px 16px 0; flex: none; max-width: 760px; width: 100%; margin: 0 auto; }
.scw-steps i { flex: 1; height: 4px; border-radius: 2px; background: var(--lss-soft2); transition: background .25s; }
.scw-steps i.on { background: var(--lss-acc); }
.scw-b { flex: 1; overflow-y: auto; overflow-x: hidden; padding: 16px; width: 100%; max-width: 760px; margin: 0 auto; }
.scw-b > * + * { margin-top: 14px; }
.scw-f { flex: none; display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; padding: 10px 16px calc(10px + env(safe-area-inset-bottom, 0px)); border-top: 1px solid var(--lss-line);
         background: var(--lss-card); }
.scw-f .btn { flex: 1 1 140px; max-width: 280px; }
@media (max-width: 600px) { .scw-f .btn { max-width: none; flex-basis: 40%; } .scw-f .btn:last-child:not(:only-child) { flex-basis: 100%; } .scw-f .btn:nth-last-child(2):first-child { flex-basis: 40%; } }
.scw h3 { margin: 0 0 6px; font-size: 15px; font-weight: 600; }
.scw .lead { font-size: 15px; line-height: 1.5; }
.scw-tips { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.scw-tips li { display: flex; gap: 12px; align-items: flex-start; padding: 10px 12px; border-radius: var(--r2); background: var(--lss-soft); font-size: 14px; line-height: 1.4; }
.scw-tips .tic { width: 32px; height: 32px; border-radius: 10px; flex: none; display: inline-flex; align-items: center; justify-content: center; background: var(--lss-tint); color: var(--lss-acc); }
.scw-cam { position: relative; width: 100%; aspect-ratio: 16 / 10; max-height: 56vh; border-radius: var(--r3); overflow: hidden; background: #05060a; }
.scw-cam video, .scw-cam canvas { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block; }
.scw-cam .guide { position: absolute; inset: 9%; border-radius: 14px; pointer-events: none; border: 2px dashed rgba(255,255,255,.35); }
.scw-cam .cap { position: absolute; left: 0; right: 0; bottom: 0; padding: 10px 14px; background: linear-gradient(transparent, rgba(0,0,0,.75)); color: #fff; font-size: 13px; }
.scw-cam .ph { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: rgba(255,255,255,.7); font-size: 14px; text-align: center; padding: 16px; }
.scw-prog { height: 10px; border-radius: 5px; background: var(--lss-soft2); overflow: hidden; }
.scw-prog > i { display: block; height: 100%; width: 0; border-radius: 5px; background: var(--lss-acc); transition: width .2s linear; }
.scw-big { font-size: 40px; font-weight: 700; text-align: center; letter-spacing: .02em; font-variant-numeric: tabular-nums; }
.scw-card { padding: 14px; border-radius: var(--r3); border: 1px solid var(--lss-line); background: var(--lss-card); }
.scw-card + .scw-card { margin-top: 12px; }
.scw-card .n { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: 50%; margin-right: 8px; font-size: 13px; background: var(--lss-acc); color: var(--lss-onacc); }
.scw-vw { position: relative; width: 100%; aspect-ratio: 4 / 3; max-height: 52vh; border-radius: var(--r3); overflow: hidden; background: #07080c; }
.scw-vw canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
.scw-vw .vt { position: absolute; top: 8px; left: 8px; right: 8px; display: flex; gap: 8px; align-items: flex-start; justify-content: space-between; pointer-events: none; }
.scw-vw .vt > * { pointer-events: auto; }
.scw-vw .seg { background: rgba(0,0,0,.55); border-color: rgba(255,255,255,.18); }
.scw-vw .segb { color: #fff; min-height: 36px; padding: 2px 12px; font-size: 13px; }
.scw-vw .segb[aria-checked=true] { color: var(--lss-onacc); }
.scw-vw .ibv { width: 44px; height: 44px; border-radius: 50%; background: rgba(0,0,0,.55); color: #fff; display: inline-flex; align-items: center; justify-content: center; border: 1px solid rgba(255,255,255,.18); }
.scw-stats { display: flex; flex-wrap: wrap; gap: 8px; }
.scw-stat { flex: 1 1 100px; padding: 10px 12px; border-radius: var(--r2); background: var(--lss-soft); }
.scw-stat b { display: block; font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
.scw-stat span { font-size: 12px; color: var(--lss-mut); }
.scw-stat.bad b { color: var(--lss-warn); }
.scw .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.scw input[type=range] { width: 100%; min-height: 44px; accent-color: var(--lss-acc); }
.scw details summary { cursor: pointer; min-height: 44px; display: flex; align-items: center; font-weight: 500; color: var(--lss-acc); }
.scw label.pick { display: flex; flex-direction: column; gap: 4px; font-size: 13px; font-weight: 600; color: var(--lss-mut); }
.scw label.pick select { width: 100%; font-weight: 400; color: var(--lss-text); }
.scw .okmsg { color: var(--lss-ok); font-weight: 600; }
.scw-meth { display: grid; gap: 8px; }
.scw-meth button { text-align: left; display: flex; flex-direction: column; gap: 2px; padding: 10px 12px; min-height: 44px; border-radius: var(--r2); cursor: pointer; font: inherit; color: var(--lss-text);
                   background: var(--lss-soft); border: 2px solid transparent; }
.scw-meth button[aria-checked=true] { border-color: var(--lss-acc); background: var(--lss-tint); }
.scw-meth button b { font-size: 15px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.scw-meth button span { font-size: 13px; color: var(--lss-mut); font-weight: 400; }
.scw-meth .rec { font-size: 11px; font-weight: 600; padding: 1px 8px; border-radius: 10px; background: var(--lss-acc); color: var(--lss-onacc); }
.scw-cam.off { position: absolute; width: 2px; height: 2px; opacity: 0; overflow: hidden; pointer-events: none; max-height: none; }
.scw-cam .ov { object-fit: fill; pointer-events: none; }
.scw-ask { position: relative; width: 100%; border-radius: var(--r3); overflow: hidden; background: #05060a; touch-action: manipulation; }
.scw-ask canvas { display: block; width: 100%; height: auto; cursor: crosshair; }
.scw-sketch { display: block; width: 100%; height: auto; border-radius: var(--r3); background: var(--lss-soft); }
.scw-chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.scw-chip { width: 22px; height: 30px; border-radius: 6px; display: inline-flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; color: #fff; background: var(--lss-ok); }
.scw-chip.manual { background: var(--lss-acc); color: var(--lss-onacc); }
.scw-chip.bad { background: var(--lss-warn); }
.scw .spin { width: 22px; height: 22px; border-radius: 50%; border: 3px solid var(--lss-soft2); border-top-color: var(--lss-acc); animation: scw-spin .8s linear infinite; display: inline-block; vertical-align: middle; margin-right: 8px; }
@keyframes scw-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .scw .spin { animation-duration: 3s !important; } }
`;

const ICON = {
  close: "M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z",
  moon: "M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a5.389 5.389 0 0 1-4.4 2.26 5.403 5.403 0 0 1-3.14-9.8c-.44-.06-.9-.1-1.36-.1z",
  hand: "M12 2a2 2 0 0 1 2 2v8h-4V4a2 2 0 0 1 2-2zm-5 9h10l-1 9a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2z",
  frame: "M3 5v4h2V5h4V3H5c-1.1 0-2 .9-2 2zm2 10H3v4c0 1.1.9 2 2 2h4v-2H5v-4zm14 4h-4v2h4c1.1 0 2-.9 2-2v-4h-2v4zm0-16h-4v2h4v4h2V5c0-1.1-.9-2-2-2z",
  eye: "M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z",
  reset: "M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 12 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z",
  cam: "M12 12c1.65 0 3-1.35 3-3s-1.35-3-3-3-3 1.35-3 3 1.35 3 3 3zm8-8h-3.17L15 2H9L7.17 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-8 13c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5z",
  save: "M17 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7l-4-4zm-5 16a3 3 0 1 1 0-6 3 3 0 0 1 0 6zm3-10H5V5h10v4z",
};
const svg = (d, s = 22) => `<svg viewBox="0 0 24 24" width="${s}" height="${s}" aria-hidden="true"><path fill="currentColor" d="${d}"/></svg>`;

export class ScanWizard {
  constructor(o) {
    this.o = o;
    this.hass = o.hass;
    this.mods = o.mods; // { Viewer3D, scan }
    this.S = o.mods.scan;
    this.lights = o.lights;
    this.runs = o.runs;
    this.entry = o.start?.entry_id || o.lights[0]?.entry_id || "";
    this.part = o.start?.run_id || ""; // "" = the whole light
    this.view = "front";
    this.method = null; // null = follow the recommendation; "point" | "glow"
    this.glowAnchors = null; // null = automatic (about 16-24 per run)
    this.glowWidth = null;
    this.hold = null;
    this.multiAngles = false; // "Scan from several angles": views are collected in this.mv and solved together
    this.mv = null; // { views: [{width, height, points: Map, orientation, found}], entry, part, redo, result }
    this.orient = null; // the latest DeviceOrientation reading, once the phone has allowed it
    this.scanMode = "colour"; // "colour" (red/green/blue frames, gentler) | "white" (the older white flashes); advanced option
    this.usedMode = "white"; // what the light actually showed in the scan that is running (the light may be an older version)
    this.camMode = "auto"; // auto | live | video
    this.step = "pick";
    this.scans = {}; // view -> { world, fillStats }
    this.closed = false;
    this._started = false;
    this._onVis = () => {
      if (document.hidden && this._capturing) this._abort("The page was hidden, so the scan stopped. Keep this screen open while scanning.");
    };
    this._onHide = () => this._stopScan(true);
    document.addEventListener("visibilitychange", this._onVis);
    window.addEventListener("pagehide", this._onHide);
    this._mount();
    this._go(o.start?.entry_id ? "aim" : "pick");
  }

  // ---- plumbing ---------------------------------------------------------------------------------------------------
  _el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  _btn(text, cls, fn, icon) {
    const b = this._el("button", cls);
    b.type = "button";
    if (icon) b.innerHTML = svg(icon, 18);
    b.append(document.createTextNode(text));
    b.onclick = fn;
    return b;
  }

  _mount() {
    const r = (this.root = this._el("div", "scw"));
    r.setAttribute("role", "dialog");
    r.setAttribute("aria-modal", "true");
    r.setAttribute("aria-label", "Scan a light to map it");
    const st = this._el("style");
    st.textContent = CSS;
    this.head = this._el("div", "scw-h");
    this.title = this._el("h2", "", "Scan");
    const x = this._el("button", "ib2");
    x.type = "button";
    x.innerHTML = svg(ICON.close);
    x.setAttribute("aria-label", "Close the scan");
    x.onclick = () => this.close();
    this.head.append(this.title, x);
    this.steps = this._el("div", "scw-steps");
    this.steps.setAttribute("aria-hidden", "true");
    for (let i = 0; i < 4; i++) this.steps.append(this._el("i"));
    this.body = this._el("div", "scw-b");
    this.body.setAttribute("aria-live", "polite");
    this.foot = this._el("div", "scw-f");
    r.append(st, this.head, this.steps, this.body, this.foot);
    this.o.host.append(r);
    this._esc = (e) => e.key === "Escape" && this.close();
    r.addEventListener("keydown", this._esc);
  }

  _go(step) {
    this._dropViewer();
    this.step = step;
    this.body.textContent = "";
    this.foot.textContent = "";
    this.body.scrollTop = 0;
    const idx = { pick: 0, aim: 1, run: 2, decode: 2, angles: 2, review: 3 }[step] ?? 0;
    [...this.steps.children].forEach((c, i) => c.classList.toggle("on", i <= idx));
    this["_s_" + step]();
    this.body.querySelector("h3,button,select")?.focus?.({ preventScroll: true });
  }

  _light() {
    return this.lights.find((l) => l.entry_id === this.entry);
  }

  // the pixel ranges being scanned: [{run, first, count}] sorted by first
  _ranges() {
    const mine = this.runs.filter((r) => r.entry_id === this.entry);
    const pick = this.part ? mine.filter((r) => r.id === this.part) : mine;
    return pick.map((r) => ({ run: r, first: r.first, count: r.count })).sort((a, b) => a.first - b.first);
  }

  _friendly(err, fallback) {
    const m = String(err?.message || err || "");
    if (err?.code === "unknown_command" || /unknown command/i.test(m)) return this._m() === "glow" ? "The glow scan needs the latest Light Show Studio (2.4.0 or newer). Update it and restart Home Assistant, then try again." : "Scanning isn't available yet. Update Light Show Studio and restart Home Assistant, then try again.";
    return m || fallback;
  }

  _ws(msg) {
    return this.hass.callWS(msg);
  }

  // ---- method: point scan (visible LEDs) or glow scan (hidden strips) -------------------------------------------------
  _recommend() {
    return this._light()?.kind === "wled" ? "glow" : "point";
  }

  _m() {
    return this.method ?? this._recommend();
  }

  _setMethod(m) {
    this.method = m;
  }

  // The code the point scan asks the light for. Colour needs the live camera: a recorded video is compressed and smears colour
  // between neighbouring LEDs, so video uses white flashes.
  _codeMode() {
    return this.scanMode === "colour" && this._mode() === "live" ? "colour" : "white";
  }

  // the stretches to light, per run being scanned: [{key, first, count, run, plan}]
  _glowPlans() {
    const o = {};
    if (this.glowAnchors) o.anchors = this.glowAnchors;
    if (this.glowWidth) o.width = this.glowWidth;
    return this._ranges().map((r) => ({ key: r.run.id, first: r.first, count: r.count, run: r.run, plan: this.S.glowPlan(r.count, o) }));
  }

  _methodCards(onChange) {
    const wrap = this._el("div", "scw-meth");
    wrap.setAttribute("role", "radiogroup");
    wrap.setAttribute("aria-label", "How to find the lights");
    const rec = this._recommend();
    const defs = [
      ["point", "Point scan", "For lights you can see as dots: strips, strings, trees. Finds every LED exactly."],
      ["glow", "Glow scan (hidden strips)", "For strips hidden under cabinets or behind covers, where the camera only sees a soft glow on the counter, floor or ceiling."],
    ];
    const draw = () => {
      wrap.textContent = "";
      for (const [id, name, desc] of defs) {
        const b = this._el("button");
        b.type = "button";
        b.setAttribute("role", "radio");
        b.setAttribute("aria-checked", String(this._m() === id));
        const t = this._el("b", "", name);
        if (id === rec) t.append(this._el("span", "rec", "Recommended"));
        b.append(t, this._el("span", "", desc));
        b.onclick = () => {
          this._setMethod(id);
          draw();
          onChange?.();
        };
        wrap.append(b);
      }
    };
    draw();
    wrap.redraw = draw;
    return wrap;
  }

  // a poor point scan: one tap to the glow scan for the same lights
  _glowOffer(text) {
    const box = this._el("div", "box mp-banner");
    box.append(this._el("strong", "", "Hidden strips?"), this._el("div", "muted", text));
    box.append(this._btn("Switch to Glow scan", "btn sm", () => {
      this._setMethod("glow");
      delete this.scans[this.view === "side" ? "side" : "front"];
      this.pos = null;
      this._go("aim");
    }));
    return box;
  }

  _fail(text, back) {
    this._go("pick");
    const e = this._el("div", "box mp-banner");
    e.setAttribute("role", "alert");
    e.append(this._el("strong", "", "That didn't work"), this._el("div", "", text));
    this.body.prepend(e);
    if (back) this.step = back;
  }

  // ---- step 1: pick -------------------------------------------------------------------------------------------------
  _s_pick() {
    this.title.textContent = "Scan a light";
    const b = this.body;
    b.append(this._el("p", "lead", "The lights flash a short code while your phone's camera watches. Each light shows up as a dot, and Light Show Studio learns where it is."));
    const lights = this.lights.filter((l) => this.runs.some((r) => r.entry_id === l.entry_id));
    if (!lights.length) {
      b.append(this._el("div", "box empty", "Add a run for a light first, then come back to scan it."));
      this.foot.append(this._btn("Close", "btn alt", () => this.close()));
      return;
    }
    if (!lights.some((l) => l.entry_id === this.entry)) this.entry = lights[0].entry_id;
    const lab = this._el("label", "pick", "Which light?");
    const sel = this._el("select");
    for (const l of lights) sel.append(Object.assign(this._el("option", "", `${l.title} (${l.bulbs} pixels)`), { value: l.entry_id }));
    sel.value = this.entry;
    lab.append(sel);
    const lab2 = this._el("label", "pick", "Which part?");
    const part = this._el("select");
    const fill = () => {
      part.textContent = "";
      part.append(Object.assign(this._el("option", "", "The whole light"), { value: "" }));
      for (const r of this.runs.filter((x) => x.entry_id === this.entry)) part.append(Object.assign(this._el("option", "", `${r.name} (${r.count} pixels)`), { value: r.id }));
      if (![...part.options].some((o) => o.value === this.part)) this.part = "";
      part.value = this.part;
      info();
    };
    const info = () => {
      const L = this._light();
      if (this._m() === "glow") {
        const plans = this._glowPlans();
        const spots = plans.reduce((a, p) => a + p.plan.anchors.length, 0);
        const secs = Math.round((spots * ((this.hold ?? 350) + 120)) / 1000 + 6);
        note.textContent = `About ${secs} seconds. A few pixels at a time light up, ${spots} spots along ${plans.length} run${plans.length === 1 ? "" : "s"}; everything else stays dark. Then the light goes back to what it showed.`;
        return;
      }
      const mode = this._codeMode();
      const frames = this.S.framesFor(this.part ? this.runs.find((r) => r.id === this.part)?.count || 2 : L?.bulbs || 2, mode);
      const secs = Math.round((frames * ((this.hold ?? 350) + 120)) / 1000 + 3);
      note.textContent = mode === "colour"
        ? `About ${secs} seconds and ${frames} colour frames (red, green and blue; no bright white flashes). All ${L?.bulbs || 0} pixels light up while it runs, then the light goes back to what it showed.`
        : `About ${secs} seconds and ${frames} white flashes. All ${L?.bulbs || 0} pixels light up while it runs, then the light goes back to what it showed.`;
    };
    const note = this._el("div", "muted");
    sel.onchange = () => {
      this.entry = sel.value;
      this.part = "";
      fill();
      cards.redraw();
      glowBox.hidden = this._m() !== "glow";
    };
    part.onchange = () => {
      this.part = part.value;
      info();
    };
    lab2.append(part);
    const cards = this._methodCards(() => {
      info();
      glowBox.hidden = this._m() !== "glow";
      this._methodHook?.();
    });
    b.append(lab, lab2, cards, note);
    fill();
    // advanced
    const det = this._el("details");
    det.append(this._el("summary", "", "More options"));
    const hl = this._el("label", "pick");
    const hv = this._el("b", "", "");
    const hr = this._el("input");
    hr.type = "range";
    hr.min = 200;
    hr.max = 900;
    hr.step = 50;
    hr.value = this.hold ?? 350;
    hr.setAttribute("aria-label", "How long each flash is held, in milliseconds");
    const showHold = () => (hv.textContent = `${hr.value} ms`);
    hr.oninput = () => {
      this.hold = Number(hr.value);
      showHold();
      info();
    };
    showHold();
    hl.append(this._el("span", "", "How long to hold each flash. Longer is safer for slow lights."), hv, hr);
    const wl = this._el("label", "pick");
    const wc = this._el("input");
    wc.type = "checkbox";
    wc.checked = this.scanMode === "white";
    wc.onchange = () => {
      this.scanMode = wc.checked ? "white" : "colour";
      info();
    };
    wl.append(wc, this._el("span", "", "Use white flashes (the older way). Colour frames are gentler and quicker; white also works with a video recording or a camera that struggles with colour."));
    const cm = this._el("div", "fld");
    cm.append(this._el("span", "lb", "Camera"));
    const secure = window.isSecureContext && !!navigator.mediaDevices?.getUserMedia;
    const opts = [["auto", secure ? "Automatic" : "Automatic (video)"], ["live", "Live camera"], ["video", "Record a video"]];
    const seg = this._el("div", "seg");
    seg.setAttribute("role", "radiogroup");
    seg.setAttribute("aria-label", "Camera mode");
    const draw = () => {
      seg.textContent = "";
      for (const [v, t] of opts) {
        const bt = this._btn(t, "segb", () => {
          this.camMode = v;
          draw();
        });
        bt.setAttribute("role", "radio");
        bt.setAttribute("aria-checked", String(this.camMode === v));
        if (v === "live" && !secure) bt.disabled = true;
        seg.append(bt);
      }
    };
    draw();
    cm.append(seg);
    if (!secure) cm.append(this._el("div", "muted", "The live camera needs a secure (https) address. Open Home Assistant over https, or record a video instead."));
    const glowBox = this._el("div", "fld");
    glowBox.hidden = this._m() !== "glow";
    const slider = (label, key, min, max, auto) => {
      const l = this._el("label", "pick");
      const v = this._el("b", "", "");
      const r = this._el("input");
      r.type = "range";
      r.min = min;
      r.max = max;
      r.step = 1;
      r.setAttribute("aria-label", label);
      const show = () => {
        const a = auto();
        v.textContent = this[key] ? String(this[key]) : `automatic (${a})`;
        r.value = this[key] || a;
      };
      r.oninput = () => {
        this[key] = Number(r.value);
        show();
        info();
      };
      show();
      l.append(this._el("span", "", label), v, r);
      return l;
    };
    const biggest = () => Math.max(1, ...this._ranges().map((r) => r.count));
    glowBox.append(
      slider("Spots to light along each run (more is slower and finer)", "glowAnchors", 6, 40, () => this.S.glowPlan(biggest()).anchors.length),
      slider("Pixels lit at each spot", "glowWidth", 3, 16, () => this.S.glowPlan(biggest(), this.glowAnchors ? { anchors: this.glowAnchors } : {}).width),
      this._btn("Back to automatic", "btn alt sm", () => {
        this.glowAnchors = this.glowWidth = null;
        this._go("pick");
      }),
    );
    det.append(wl, hl, glowBox, cm);
    // several angles (point method only)
    const ml = this._el("label", "pick");
    const mc = this._el("input");
    mc.type = "checkbox";
    mc.checked = this.multiAngles;
    ml.append(mc, this._el("span", "", "Scan from several angles (3 to 6 spots) for a full 3D map. Best for trees and lights that wrap round a corner; each angle takes about a minute."));
    const showMulti = () => (ml.hidden = this._m() === "glow" || !this.mods.multiview);
    showMulti();
    mc.onchange = () => (this.multiAngles = mc.checked);
    note.after(ml);
    this._methodHook = showMulti;
    b.append(det);
    this.foot.append(
      this._btn("Cancel", "btn alt", () => this.close()),
      this._btn("Next", "btn", async () => {
        const multi = this.multiAngles && this._m() === "point";
        this.multiAngles = multi;
        if (multi) {
          if (!this.mv || this.mv.entry !== this.entry || this.mv.part !== this.part) this.mv = { views: [], redo: -1, entry: this.entry, part: this.part };
          await this._askOrientation(); // called inside the tap: iOS only asks for motion access in answer to one
        }
        this._go("aim");
      }),
    );
  }

  // ---- step 2: aim / start ------------------------------------------------------------------------------------------------
  _mode() {
    const secure = window.isSecureContext && !!navigator.mediaDevices?.getUserMedia;
    if (this.camMode === "live") return secure ? "live" : "video";
    if (this.camMode === "video") return "video";
    return secure ? "live" : "video";
  }

  _s_aim() {
    const L = this._light();
    const side = this.view === "side" && !this.multiAngles;
    const k = this.multiAngles ? (this.mv.redo >= 0 ? this.mv.redo : this.mv.views.length) : 0;
    this.title.textContent = this.multiAngles ? `Angle ${k + 1}` : side ? "Scan from the side" : `Scan ${L?.title || "the light"}`;
    const b = this.body;
    if (!L) return this._go("pick");
    const glow = this._m() === "glow";
    b.append(this._el("h3", "", this.multiAngles ? (k === 0 ? "Stand in front of the lights" : this._angleHint(k)) : side ? "Walk about a quarter of the way round, to the right of the lights" : glow ? "Face the surfaces the lights shine on" : "Stand in front of the lights"));
    // which method, and a one-tap switch
    const mrow = this._el("div", "row");
    mrow.append(this._el("span", "muted", glow ? "Glow scan: for hidden strips." : this.multiAngles ? "Point scan from several angles." : "Point scan: for lights you can see."));
    if (!this.multiAngles) {
      mrow.append(this._btn(glow ? "Use Point scan" : "Use Glow scan", "btn alt sm", () => {
        this._setMethod(glow ? "point" : "glow");
        this._go("aim");
      }));
    }
    b.append(mrow);
    const tips = this._el("ul", "scw-tips");
    const tip = (icon, text) => {
      const li = this._el("li");
      const ic = this._el("span", "tic");
      ic.innerHTML = svg(icon, 18);
      li.append(ic, this._el("span", "", text));
      tips.append(li);
    };
    if (!glow && this._codeMode() === "colour") tip(ICON.moon, "Dim the room lights; it doesn't have to be pitch dark. Switch off bright screens and turn lamps away from the camera.");
    else tip(ICON.moon, "Use a dark room or wait until night. Switch off other bright lights and screens.");
    if (glow) {
      tip(ICON.frame, side ? "Keep the same surfaces in view, from about 90 degrees around. Stay at the same distance." : "Fit all the glow in the picture: the counter, floor or ceiling the strips light up. You don't need to see the strips.");
      tip(ICON.hand, "Hold the phone steady, or lean it on something. Don't move until it finishes.");
      tip(ICON.eye, "A few pixels light at a time and the soft patch of light they make is tracked along. If one can't be found it asks you to tap where the glow is.");
    } else {
      tip(ICON.frame, this.multiAngles && k > 0 ? "Keep the whole light in view again and stay at about the same distance. Parts hidden from this spot can stay hidden: lights seen from two spots get a 3D place." : side ? "Keep the whole light in view again, from about 90 degrees around. Stay at the same distance." : "Fit all of the light in the picture, with a little space around it.");
      tip(ICON.hand, "Hold the phone steady, or lean it on something. Don't move until it finishes.");
      tip(ICON.eye, this._codeMode() === "colour" ? "Use the back camera. The lights show red, green and blue, so keep the phone's colour filters off. Every pixel you can see will be found; ones hidden behind things are guessed." : "Use the back camera. Every pixel you can see will be found; ones hidden behind things are guessed.");
    }
    this._tips = tips;
    const mode = this._mode();
    if (mode === "live") return this._aimLive();
    this._aimVideo();
  }

  // live camera
  async _openCamera() {
    this._closeCamera();
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: MAX_W }, height: { ideal: 1080 } },
      audio: false,
    });
    if (this.closed) {
      stream.getTracks().forEach((t) => t.stop());
      throw new Error("closed");
    }
    this.stream = stream;
    try {
      // a fixed exposure keeps the dim and bright frames comparable, where the phone allows it
      await stream.getVideoTracks()[0].applyConstraints({ advanced: [{ exposureMode: "continuous" }] });
    } catch (e) {
      /* not supported: fine */
    }
    return stream;
  }

  _closeCamera() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
  }

  _aimLive() {
    const b = this.body;
    const cam = this._el("div", "scw-cam");
    const ph = this._el("div", "ph");
    ph.innerHTML = `${svg(ICON.cam, 40)}<div>Tap <b>Open camera</b> to point your phone at the lights.</div>`;
    const video = this._el("video");
    video.muted = true;
    video.playsInline = true;
    video.setAttribute("playsinline", "");
    video.hidden = true;
    const guide = this._el("div", "guide");
    guide.hidden = true;
    cam.append(video, guide, ph);
    this.video = video;
    const err = this._el("div", "serr");
    err.hidden = true;
    err.setAttribute("role", "alert");
    b.append(cam, err, this._tips);
    const go = this._btn("Start scan", "btn", () => (this._m() === "glow" ? this._runGlowLive() : this._runLive()), ICON.cam);
    go.disabled = true;
    const open = this._btn("Open camera", "btn alt", async () => {
      open.disabled = true;
      err.hidden = true;
      try {
        const s = await this._openCamera();
        video.srcObject = s;
        await video.play();
        video.hidden = guide.hidden = false;
        ph.hidden = true;
        go.disabled = false;
        open.hidden = true;
      } catch (e) {
        open.disabled = false;
        if (this.closed) return;
        const n = e?.name || "";
        err.textContent = n === "NotAllowedError" || n === "SecurityError"
          ? "The camera is blocked. Allow camera access for this page in your browser settings, then try again, or record a video instead."
          : n === "NotFoundError" || n === "OverconstrainedError"
            ? "No camera was found on this device. Record a video instead."
            : `The camera didn't start (${e?.message || n}). You can record a video instead.`;
        err.hidden = false;
        useVideo.hidden = false;
      }
    }, ICON.cam);
    const useVideo = this._btn("Record a video instead", "btn alt", () => {
      this._closeCamera();
      this.camMode = "video";
      this._go("aim");
    });
    useVideo.hidden = true;
    this.foot.append(this._btn("Back", "btn alt", () => (this._closeCamera(), this._go(this._backStep(false)))), useVideo, open, go);
    this._goBtn = go;
  }

  // ---- live scan ----------------------------------------------------------------------------------------------------------------
  _hold(mode) {
    return this.hold ?? (mode === "video" ? 500 : 350);
  }

  // a single run is coded on its own (pixel first + i is code index i); the whole light otherwise
  _scope() {
    const run = this.part ? this.runs.find((r) => r.id === this.part) : null;
    return run ? { first: run.first, count: run.count } : { first: 0, count: this._light().bulbs };
  }

  async _scanStart() {
    const sc = this._scope();
    const msg = { type: "light_show_studio/map/scan/start", entry_id: this.entry };
    if (this.part) Object.assign(msg, sc);
    if (this._m() !== "glow") msg.mode = this._codeMode();
    this._orientAtScan = this.orient ? { ...this.orient } : null; // the phone is held still for the whole scan
    const r = await this._ws(msg);
    this._started = true;
    this.base = sc.first;
    this.N = sc.count;
    if (this._m() === "glow") return r;
    this.usedMode = r?.mode === "colour" ? "colour" : "white"; // an older light has no mode and always flashes white
    const mine = this.S.framesFor(this.N, this.usedMode);
    this.F = Number(r?.frames) || mine;
    if (this.F !== mine) throw new Error(`The light and the panel disagree about the code (${this.F} vs ${mine} frames). Update Light Show Studio and reload this page.`);
    return r;
  }

  async _stopScan(quiet) {
    if (!this._started) return;
    this._started = false;
    try {
      await this._ws({ type: "light_show_studio/map/scan/stop", entry_id: this.entry });
    } catch (e) {
      if (!quiet) this._stopErr = true;
    }
  }

  _abort(text) {
    this._aborted = text;
  }

  // one camera picture: {w, h, luma} or, with rgb, {w, h, rgb} (3 bytes per pixel, for colour codes)
  _grab(video, cv, rgb = false) {
    const vw = video.videoWidth, vh = video.videoHeight;
    if (!vw || !vh) return null;
    const k = Math.min(1, MAX_W / vw);
    const w = Math.round(vw * k), h = Math.round(vh * k);
    if (cv.width !== w) cv.width = w;
    if (cv.height !== h) cv.height = h;
    const g = cv.getContext("2d", { willReadFrequently: true });
    g.drawImage(video, 0, 0, w, h);
    const px = g.getImageData(0, 0, w, h).data;
    return rgb ? { w, h, rgb: this.S.rgbFromRGBA(px, w, h) } : { w, h, luma: this.S.lumaFromRGBA(px, w, h) };
  }

  // colour frames are read by chromaticity, so the phone must not re-balance colours between frames: pin the white balance
  async _pinWhiteBalance() {
    try {
      await this.stream?.getVideoTracks()[0].applyConstraints({ advanced: [{ whiteBalanceMode: "manual", colorTemperature: 4500 }] });
    } catch (e) {
      /* not supported: the per-light calibration frames still cover a steady balance */
    }
  }

  async _runLive() {
    const video = this.video;
    if (!video || !this.stream) return;
    const hold = this._hold("live");
    const keepVideo = video;
    this._aborted = null;
    this._capturing = true;
    // show progress under the camera picture
    this.foot.textContent = "";
    const cancel = this._btn("Cancel", "btn alt", () => this._abort("Cancelled."));
    this.foot.append(cancel);
    const prog = this._el("div", "scw-prog");
    const bar = this._el("i");
    prog.append(bar);
    const status = this._el("div", "muted", "Starting. Hold the phone steady.");
    status.setAttribute("role", "status");
    this.body.textContent = "";
    this.body.append(this._el("h3", "", "Scanning. Keep the phone still."));
    const cam = this._el("div", "scw-cam");
    cam.append(keepVideo);
    keepVideo.hidden = false;
    cam.append(this._el("div", "guide"));
    const cap = this._el("div", "cap", "");
    cam.append(cap);
    this.body.append(cam, prog, status);
    const cv = document.createElement("canvas");
    const frames = [];
    let size = null;
    try {
      await this._scanStart();
      const colour = this.usedMode === "colour", key = colour ? "rgb" : "luma", settle = colour ? 4 : 2;
      if (colour) await this._pinWhiteBalance();
      for (let k = 0; k < this.F; k++) {
        if (this._aborted) throw new Error(this._aborted);
        cap.textContent = colour ? `Frame ${k + 1} of ${this.F}` : `Flash ${k + 1} of ${this.F}`;
        status.textContent = k < settle ? "Setting the exposure…" : "Reading the code…";
        bar.style.width = `${Math.round((k / this.F) * 100)}%`;
        await this._ws({ type: "light_show_studio/map/scan/frame", entry_id: this.entry, frame: k });
        await sleep(hold + (k < settle ? 350 : 0));
        const a = this._grab(video, cv, colour);
        if (!a) throw new Error("The camera picture isn't ready. Try again.");
        await sleep(45);
        const b2 = this._grab(video, cv, colour);
        if (size && (a.w !== size.w || a.h !== size.h)) throw new Error("The camera changed size during the scan. Try again.");
        size = { w: a.w, h: a.h };
        // two grabs averaged: less noise
        const mix = new Uint8ClampedArray(a[key].length);
        for (let i = 0; i < mix.length; i++) mix[i] = (a[key][i] + (b2 ? b2[key][i] : a[key][i]) + 1) >> 1;
        frames.push(mix);
      }
      bar.style.width = "100%";
    } catch (e) {
      this._capturing = false;
      await this._stopScan(true);
      this._closeCamera();
      if (this.closed) return;
      return this._failScan(this._aborted === "Cancelled." ? "" : this._friendly(e, "The scan stopped."));
    }
    this._capturing = false;
    await this._stopScan(true);
    this._closeCamera();
    if (this.closed) return;
    this._decode(frames, size.w, size.h);
  }

  _failScan(text, offerGlow) {
    if (!text) return this._go(this._backStep(true));
    this._go("aim");
    const e = this._el("div", "box mp-banner");
    e.setAttribute("role", "alert");
    e.append(this._el("strong", "", "The scan didn't finish"), this._el("div", "", text));
    this.body.prepend(e);
    if (offerGlow && this._m() === "point") e.after(this._glowOffer("If these lights are hidden (under cabinets, behind covers), the camera only sees a glow, not dots. The Glow scan follows that glow instead."));
  }

  // ---- glow scan (hidden strips) ------------------------------------------------------------------------------------------------
  // The lights show one item at a time: a dark reference, or a short lit stretch of one run. See light-show-studio-scan.js (glow*).
  async _showItem(it) {
    if (it.kind === "seg") return this._ws({ type: "light_show_studio/map/scan/segment", entry_id: this.entry, start: it.start, count: it.count });
    return this._ws({ type: "light_show_studio/map/scan/frame", entry_id: this.entry, frame: 0 });
  }

  _glowSetup() {
    const plans = this._glowPlans();
    const items = this.S.glowSchedule(plans.map((p) => ({ key: p.key, first: p.first, plan: p.plan })));
    this._glow = { plans, items, res: Object.fromEntries(plans.map((p) => [p.key, new Array(p.plan.anchors.length).fill(null)])) };
    return this._glow;
  }

  // a camera picture shrunk for the glow search (two grabs averaged); .W/.H are the full picture's size
  async _grabGlow(video, cv) {
    const S = this.S, a = this._grab(video, cv);
    if (!a) return null;
    const f = S.glowFactor(a.w), out = S.downscaleLuma(a.luma, a.w, a.h, f);
    await sleep(45);
    const b = this._grab(video, cv);
    if (b && b.w === a.w && b.h === a.h) {
      const o2 = S.downscaleLuma(b.luma, b.w, b.h, f);
      for (let i = 0; i < out.data.length; i++) out.data[i] = (out.data[i] + o2.data[i]) / 2;
    }
    out.W = a.w;
    out.H = a.h;
    return out;
  }

  // find the glow of one lit stretch and note it; good = found clearly enough to use
  _glowMeasure(it, dark, lit) {
    const g = this.S.findGlow(dark.data, lit.data, lit.w, lit.h, { f: lit.f });
    this._glow.res[it.key][it.a] = { x: g.x, y: g.y, conf: g.conf, reason: g.reason };
    return { g, good: !g.reason && g.conf >= this.S.GLOW.minConf };
  }

  // Pause and ask: show the frozen glow picture, the person taps the middle of the glow (or skips this spot).
  // Resolves "use" | "skip" | "retry" | "stop". live: the lit stretch is still on, so Try again is possible.
  _askAnchor(it, dark, lit, g, opts = {}) {
    const S = this.S;
    return new Promise((resolve) => {
      const plan = this._glow.plans.find((p) => p.key === it.key);
      const total = plan.plan.anchors.length;
      const box = this._el("div", "scw-card");
      box.setAttribute("role", "group");
      const why = g.reason === "wide" ? "The light spread over the whole picture." : g.reason ? "No clear glow was seen." : "The glow was faint or there were two patches.";
      box.append(this._el("h3", "", `Where is the glow? ${plan.run.name}, spot ${it.a + 1} of ${total}`));
      box.append(this._el("div", "muted", `${why} Tap the middle of the glow on the picture${g.x != null ? ", or use the cross it found" : ""}. Or skip this spot: the pixels around it are worked out from the other spots.`));
      const wrap = this._el("div", "scw-ask");
      const cv = this._el("canvas");
      cv.width = lit.w;
      cv.height = lit.h;
      cv.setAttribute("role", "img");
      cv.setAttribute("aria-label", "The camera picture with the glow bright. Tap the middle of the glow.");
      wrap.append(cv);
      box.append(wrap);
      const cg = cv.getContext("2d");
      const base = new ImageData(S.glowPreview(dark.data, lit.data, lit.w, lit.h), lit.w, lit.h);
      let pick = g.x != null ? { x: g.x, y: g.y } : null;
      const draw = () => {
        cg.putImageData(base, 0, 0);
        if (!pick) return;
        const px = (pick.x + 0.5) / lit.f - 0.5, py = (pick.y + 0.5) / lit.f - 0.5, r = Math.max(5, lit.w * 0.025);
        cg.lineWidth = Math.max(1.5, lit.w / 240);
        cg.strokeStyle = "#ff9d00";
        cg.beginPath();
        cg.arc(px + 0.5, py + 0.5, r, 0, 6.2832);
        cg.moveTo(px + 0.5 - r * 1.6, py + 0.5);
        cg.lineTo(px + 0.5 + r * 1.6, py + 0.5);
        cg.moveTo(px + 0.5, py + 0.5 - r * 1.6);
        cg.lineTo(px + 0.5, py + 0.5 + r * 1.6);
        cg.stroke();
      };
      draw();
      let use;
      cv.onpointerup = (ev) => {
        const rc = cv.getBoundingClientRect();
        const sx = ((ev.clientX - rc.left) / rc.width) * lit.w, sy = ((ev.clientY - rc.top) / rc.height) * lit.h;
        const t = S.refineTap(dark.data, lit.data, lit.w, lit.h, sx * lit.f - 0.5, sy * lit.f - 0.5, { f: lit.f });
        pick = { x: t.x, y: t.y };
        use.disabled = false;
        draw();
      };
      const prevFoot = [...this.foot.children];
      const ka = opts.live ? setInterval(() => this._showItem(it).catch(() => {}), 20000) : 0; // keep the scan awake while the person looks
      const done = (act) => {
        clearInterval(ka);
        box.remove();
        this.foot.textContent = "";
        this.foot.append(...prevFoot);
        if (act === "use") this._glow.res[it.key][it.a] = { x: pick.x, y: pick.y, conf: g.conf || 0, manual: true };
        if (act === "skip") this._glow.res[it.key][it.a] = { x: null, y: null, conf: 0, skipped: true };
        resolve(act);
      };
      this.foot.textContent = "";
      use = this._btn("Use this spot", "btn", () => done("use"));
      use.disabled = !pick;
      this.foot.append(this._btn("Stop scan", "btn alt", () => done("stop")), this._btn("Skip this spot", "btn alt", () => done("skip")));
      if (opts.live) this.foot.append(this._btn("Try again", "btn alt", () => done("retry")));
      this.foot.append(use);
      opts.host.after(box);
      box.scrollIntoView?.({ block: "nearest" });
    });
  }

  async _runGlowLive() {
    const video = this.video;
    if (!video || !this.stream) return;
    const S = this.S, hold = this._hold("live");
    this._aborted = null;
    this._capturing = true;
    this.foot.textContent = "";
    this.foot.append(this._btn("Cancel", "btn alt", () => this._abort("Cancelled.")));
    const prog = this._el("div", "scw-prog");
    const bar = this._el("i");
    prog.append(bar);
    const status = this._el("div", "muted", "Starting. Hold the phone steady.");
    status.setAttribute("role", "status");
    this.body.textContent = "";
    const h3 = this._el("h3", "", "Scanning. Keep the phone still.");
    this.body.append(h3);
    const cam = this._el("div", "scw-cam");
    video.hidden = false;
    const ov = this._el("canvas", "ov");
    const cap = this._el("div", "cap", "");
    cam.append(video, ov, cap);
    this.body.append(cam, prog, status);
    const cv = document.createElement("canvas");
    const G = this._glowSetup();
    const darks = new Map();
    let size = null;
    // found centres drawn over the live picture (the video is shown "cover": same crop here)
    const drawOv = (now) => {
      const cw = cam.clientWidth, ch = cam.clientHeight;
      if (!cw || !ch || !size) return;
      if (ov.width !== cw) ov.width = cw;
      if (ov.height !== ch) ov.height = ch;
      const g = ov.getContext("2d");
      g.clearRect(0, 0, cw, ch);
      const sc = Math.max(cw / video.videoWidth, ch / video.videoHeight), k = size.W / video.videoWidth;
      const ox = (cw - video.videoWidth * sc) / 2, oy = (ch - video.videoHeight * sc) / 2;
      for (const list of Object.values(G.res)) {
        for (const r of list) {
          if (!r || r.x == null) continue;
          g.beginPath();
          g.arc(ox + (r.x / k) * sc, oy + (r.y / k) * sc, 5, 0, 6.2832);
          g.fillStyle = r.manual ? "#4aa3ff" : r.conf >= S.GLOW.minConf ? "#37d67a" : "#ff9d00";
          g.fill();
        }
      }
      void now;
    };
    try {
      await this._scanStart();
      for (let n = 0; n < G.items.length; n++) {
        if (this._aborted) throw new Error(this._aborted);
        const it = G.items[n];
        const plan = it.kind === "seg" ? G.plans.find((p) => p.key === it.key) : null;
        cap.textContent = plan ? `${plan.run.name}: spot ${it.a + 1} of ${plan.plan.anchors.length}` : "Reference picture (all dark)";
        status.textContent = n < 2 ? "Setting the exposure…" : "Following the glow along the lights…";
        bar.style.width = `${Math.round((n / G.items.length) * 100)}%`;
        await this._showItem(it);
        await sleep(hold + (n < 2 ? 350 : 0));
        let small = await this._grabGlow(video, cv);
        if (!small) throw new Error("The camera picture isn't ready. Try again.");
        if (size && (small.W !== size.W || small.H !== size.H)) throw new Error("The camera changed size during the scan. Try again.");
        size = { W: small.W, H: small.H };
        if (it.kind === "dark") {
          darks.set(n, small);
          continue;
        }
        const dark = darks.get(it.dark);
        let out = this._glowMeasure(it, dark, small);
        drawOv();
        while (!out.good) {
          h3.textContent = "Needs your help";
          cam.classList.add("off"); // the camera keeps running, out of sight, while the frozen picture is shown
          const act = await this._askAnchor(it, dark, small, out.g, { live: true, host: cam });
          cam.classList.remove("off");
          h3.textContent = "Scanning. Keep the phone still.";
          if (this._aborted) throw new Error(this._aborted);
          if (act === "stop") throw new Error("Cancelled.");
          if (act !== "retry") break;
          await this._showItem(it);
          await sleep(hold + 250);
          small = await this._grabGlow(video, cv);
          if (!small) throw new Error("The camera picture isn't ready. Try again.");
          out = this._glowMeasure(it, dark, small);
        }
        drawOv();
      }
      bar.style.width = "100%";
    } catch (e) {
      this._capturing = false;
      await this._stopScan(true);
      this._closeCamera();
      if (this.closed) return;
      return this._failScan(this._aborted === "Cancelled." || e.message === "Cancelled." ? "" : this._friendly(e, "The scan stopped."));
    }
    this._capturing = false;
    await this._stopScan(true);
    this._closeCamera();
    if (this.closed) return;
    this._glowFinish();
  }

  // solve every run from its anchors and show the review
  _glowFinish() {
    try {
      const S = this.S, G = this._glow;
      const parts = [], ranges = [], infos = [], dropped = [];
      for (const p of G.plans) {
        const anchors = p.plan.anchors.map((a, k) => ({ c: a.c, ...(G.res[p.key][k] || {}) }));
        const sol = S.solveGlow(p.count, anchors);
        infos.push({ run: p.run, first: p.first, plan: p.plan, sol });
        if (!sol.ok) {
          dropped.push(p.run.name);
          continue;
        }
        parts.push(sol);
        ranges.push({ run: p.run, first: p.first, count: p.count });
      }
      if (!parts.length) throw new Error("Not enough of the glow could be followed. Try a darker room, hold the phone steadier, point it at the glowing surfaces, or tap the glow when asked.");
      if (this.view === "side" && this.scans.front && this.scans.front.ranges.map((r) => r.run.id).join() !== ranges.map((r) => r.run.id).join()) {
        throw new Error(`The side scan found different runs from the front scan (missing: ${dropped.join(", ") || "none"}). Scan again so the same runs are followed.`);
      }
      const total = parts.reduce((a, p) => a + p.x.length, 0);
      const cat = { x: new Float64Array(total), y: new Float64Array(total), est: new Uint8Array(total) };
      let o = 0, okA = 0, allA = 0, guessed = 0;
      for (const p of parts) {
        cat.x.set(p.x, o);
        cat.y.set(p.y, o);
        cat.est.set(p.est, o);
        o += p.x.length;
        okA += p.found;
        guessed += p.est.reduce((a, v) => a + v, 0);
      }
      for (const i of infos) allA += i.plan.anchors.length;
      const world = S.toWorld(cat);
      this._remerge = true;
      this.scans[this.view] = { glow: true, world, found: okA, total: allA, guessed, px: total, ranges, infos, dropped };
      this._go("review");
    } catch (e) {
      if (this.closed) return;
      this._failScan(e.message || "Couldn't follow the glow.");
    }
  }

  // ---- video fallback ------------------------------------------------------------------------------------------------------------------
  _aimVideo() {
    const b = this.body;
    b.append(this._tips);
    const hold = this._hold("video");
    const insecure = !window.isSecureContext;
    b.append(this._el("div", "muted", insecure
      ? "This page isn't on a secure (https) address, so the browser won't give it the live camera. Film the lights with your phone's camera instead: the lights run a sequence and the video is read afterwards."
      : "You chose to record a video. The lights run a sequence and the video is read afterwards."));
    const c1 = this._el("div", "scw-card");
    c1.append(this._el("h3", "", ""));
    c1.firstChild.innerHTML = `<span class="n">1</span>Start the lights`;
    c1.append(this._el("div", "muted", "Press the button, then start filming. The lights stay off for 10 seconds, flash three times (so the video can be lined up), then run the code. Keep filming until it says Finished."));
    c1.append(this._el("div", "muted", "A single phone can't film and run this page at once, because the page pauses while the camera app is open. Run this on a laptop or a second device, and film with your phone."));
    const prog = this._el("div", "scw-prog");
    const bar = this._el("i");
    prog.append(bar);
    prog.hidden = true;
    const big = this._el("div", "scw-big", "");
    big.hidden = true;
    big.setAttribute("role", "timer");
    const st = this._el("div", "muted", "");
    st.setAttribute("role", "status");
    const startBtn = this._btn("Start the sequence", "btn", () => this._runSequence({ startBtn, big, prog, bar, st, hold }), ICON.save);
    startBtn.innerHTML = svg(ICON.eye, 18) + "Start the sequence";
    c1.append(startBtn, big, prog, st);
    const c2 = this._el("div", "scw-card");
    c2.append(this._el("h3", "", ""));
    c2.firstChild.innerHTML = `<span class="n">2</span>Give it the video`;
    c2.append(this._el("div", "muted", "On the phone that filmed it, open this page, pick the same light, and choose the video. Everything stays on your device while it is read."));
    const err = this._el("div", "serr");
    err.hidden = true;
    err.setAttribute("role", "alert");
    const mk = (label, capture) => {
      const lab = this._el("label", "btn alt");
      lab.style.cursor = "pointer";
      lab.append(document.createTextNode(label));
      const inp = this._el("input");
      inp.type = "file";
      inp.accept = "video/*";
      if (capture) inp.setAttribute("capture", "environment");
      inp.style.cssText = "position:absolute;width:1px;height:1px;opacity:0;pointer-events:none";
      inp.onchange = () => {
        const f = inp.files?.[0];
        inp.value = "";
        if (f) this._decodeVideo(f, hold, err);
      };
      lab.append(inp);
      lab.style.position = "relative";
      return lab;
    };
    const row = this._el("div", "row");
    row.append(mk("Record a video now", true), mk("Choose a video", false));
    c2.append(row, err);
    b.append(c1, c2);
    this.foot.append(this._btn("Back", "btn alt", () => this._go(this._backStep(false))));
  }

  // the lights run: 10 s dark, three sync blinks, then every code frame on an absolute schedule
  async _runSequence(ui) {
    const { startBtn, big, prog, bar, st, hold } = ui;
    startBtn.disabled = true;
    big.hidden = prog.hidden = false;
    this._aborted = null;
    this._capturing = true;
    const cancel = this._btn("Cancel", "btn alt", () => this._abort("Cancelled."));
    this.foot.append(cancel);
    try {
      await this._scanStart();
      await this._ws({ type: "light_show_studio/map/scan/frame", entry_id: this.entry, frame: 0 });
      for (let s = 10; s > 0; s--) {
        if (this._aborted) throw new Error(this._aborted);
        big.textContent = String(s);
        st.textContent = "Start filming now. The lights flash in a moment.";
        await sleep(1000);
      }
      const SY = this.S.SYNC;
      const t0 = performance.now();
      const at = async (ms) => {
        const d = t0 + ms - performance.now();
        if (d > 0) await sleep(d);
      };
      let t = 0;
      big.textContent = "Flashing";
      for (let i = 0; i < SY.blinks; i++) {
        await at(t);
        await this._ws({ type: "light_show_studio/map/scan/frame", entry_id: this.entry, frame: 1 });
        t += SY.onMs;
        await at(t);
        await this._ws({ type: "light_show_studio/map/scan/frame", entry_id: this.entry, frame: 0 });
        t += SY.offMs;
      }
      const G = this._m() === "glow" ? this._glowSetup() : null;
      const count = G ? G.items.length : this.F;
      for (let k = 0; k < count; k++) {
        if (this._aborted) throw new Error(this._aborted);
        await at(this.S.videoFrameStart(k, hold));
        big.textContent = `${k + 1} / ${count}`;
        bar.style.width = `${Math.round((k / count) * 100)}%`;
        st.textContent = "Keep filming until it says Finished.";
        if (G) await this._showItem(G.items[k]);
        else await this._ws({ type: "light_show_studio/map/scan/frame", entry_id: this.entry, frame: k });
      }
      await sleep(hold + 400);
      bar.style.width = "100%";
      big.textContent = "Finished";
      st.textContent = "You can stop filming now.";
    } catch (e) {
      st.textContent = this._aborted === "Cancelled." ? "Cancelled." : this._friendly(e, "The sequence stopped.");
      st.style.color = "var(--lss-err)";
    }
    this._capturing = false;
    await this._stopScan(true);
    startBtn.disabled = false;
    startBtn.lastChild.textContent = "Run it again";
    cancel.remove();
  }

  async _decodeVideo(file, hold, errEl) {
    errEl.hidden = true;
    const S = this.S;
    this._go("decode");
    const msg = this._decodeUi("Reading the video…");
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    video.src = url;
    const cleanup = () => {
      URL.revokeObjectURL(url);
      video.removeAttribute("src");
      video.load();
    };
    const wait = (ev, ms) =>
      new Promise((res, rej) => {
        const t = setTimeout(() => rej(new Error("The video took too long to load.")), ms);
        video.addEventListener(ev, () => (clearTimeout(t), res()), { once: true });
        video.addEventListener("error", () => (clearTimeout(t), rej(new Error("That video can't be read by this browser."))), { once: true });
      });
    const seek = async (t) => {
      const p = wait("seeked", 6000);
      video.currentTime = Math.max(0, Math.min(t, video.duration - 0.01));
      await p;
    };
    try {
      if (video.readyState < 1) await wait("loadedmetadata", 20000);
      if (!Number.isFinite(video.duration)) {
        // some recorders write no length: seeking far past the end makes the browser work it out
        await new Promise((res) => {
          const t = setTimeout(res, 5000);
          video.addEventListener("timeupdate", () => (clearTimeout(t), res()), { once: true });
          video.currentTime = 1e7;
        });
      }
      const dur = video.duration;
      if (!Number.isFinite(dur) || dur < 8) throw new Error("The video is too short. It should cover the whole sequence.");
      const vw = video.videoWidth, vh = video.videoHeight;
      const k = Math.min(1, MAX_W / vw), w = Math.round(vw * k), h = Math.round(vh * k);
      const small = document.createElement("canvas");
      small.width = 96;
      small.height = Math.max(8, Math.round((96 * vh) / vw));
      const sg = small.getContext("2d", { willReadFrequently: true });
      const cv = document.createElement("canvas");
      cv.width = w;
      cv.height = h;
      const g = cv.getContext("2d", { willReadFrequently: true });
      const bright = async (t) => {
        await seek(t);
        sg.drawImage(video, 0, 0, small.width, small.height);
        return S.topBrightness(S.lumaFromRGBA(sg.getImageData(0, 0, small.width, small.height).data, small.width, small.height));
      };
      // 1. find the sync blinks
      const samples = [];
      const limit = Math.min(dur, 70);
      for (let t = 0; t < limit; t += 0.2) {
        if (this.closed) return cleanup();
        samples.push({ t, v: await bright(t) });
        msg.set(`Looking for the sync flashes… ${Math.round((t / limit) * 100)}%`, (t / limit) * 0.3);
        if (t > 16 && S.findSync(samples)) break;
      }
      const edge = S.findSync(samples);
      if (!edge) throw new Error("Couldn't find the three sync flashes at the start. Start filming before pressing Start, keep the lights in view, and use a dark room.");
      // 2. sharpen the time of the first flash by bisection
      let [lo, hi] = edge.bracket;
      const vlo = await bright(lo), vhi = await bright(hi), vs = samples.map((s) => s.v).sort((a, b) => a - b);
      const thr = vs[Math.floor(vs.length * 0.1)] + 0.5 * (vs[Math.floor(vs.length * 0.98)] - vs[Math.floor(vs.length * 0.1)]);
      if (vlo < thr && vhi >= thr) {
        for (let i = 0; i < 6; i++) {
          const mid = (lo + hi) / 2;
          if ((await bright(mid)) >= thr) hi = mid;
          else lo = mid;
        }
      }
      const t0 = (lo + hi) / 2;
      // 3. one picture per code frame
      const sc = this._scope(), N = sc.count, F = S.scanFrames(N);
      this.base = sc.first;
      this.N = N;
      if (this._m() === "glow") {
        const G = this._glowSetup(), darks = new Map(), pending = [];
        for (let k = 0; k < G.items.length; k++) {
          if (this.closed) return cleanup();
          const tt = t0 + (S.videoFrameStart(k, hold) + hold * 0.6) / 1000;
          if (tt > dur) throw new Error("The video stops before the sequence ends. Keep filming until the screen says Finished.");
          await seek(tt);
          g.drawImage(video, 0, 0, w, h);
          const small = S.downscaleLuma(S.lumaFromRGBA(g.getImageData(0, 0, w, h).data, w, h), w, h);
          small.W = w;
          small.H = h;
          const it = G.items[k];
          if (it.kind === "dark") darks.set(k, small);
          else {
            const out = this._glowMeasure(it, darks.get(it.dark), small);
            if (!out.good) pending.push({ it, dark: darks.get(it.dark), lit: small, g: out.g });
          }
          msg.set(`Following the glow… ${k + 1} of ${G.items.length}`, 0.3 + 0.6 * ((k + 1) / G.items.length));
        }
        cleanup();
        if (pending.length) msg.set(`${pending.length} spot${pending.length === 1 ? "" : "s"} need your help`, 0.95);
        const spin = this.body.querySelector(".scw-prog");
        for (const q of pending) {
          if (this.closed || this._decodeCancel) return;
          const act = await this._askAnchor(q.it, q.dark, q.lit, q.g, { live: false, host: spin });
          if (act === "stop") return this._go("pick");
        }
        return this._glowFinish();
      }
      this.F = F;
      this.usedMode = "white"; // recorded video: white flashes (see _codeMode)
      const frames = [];
      for (let f = 0; f < F; f++) {
        if (this.closed) return cleanup();
        const tt = t0 + (S.videoFrameStart(f, hold) + hold * 0.6) / 1000;
        if (tt > dur) throw new Error("The video stops before the sequence ends. Keep filming until the screen says Finished.");
        await seek(tt);
        g.drawImage(video, 0, 0, w, h);
        frames.push(S.lumaFromRGBA(g.getImageData(0, 0, w, h).data, w, h));
        msg.set(`Reading flash ${f + 1} of ${F}…`, 0.3 + 0.6 * ((f + 1) / F));
      }
      cleanup();
      this._decode(frames, w, h, true);
    } catch (e) {
      cleanup();
      if (this.closed) return;
      this._go("aim");
      const box = this._el("div", "box mp-banner");
      box.setAttribute("role", "alert");
      box.append(this._el("strong", "", "Couldn't read the video"), this._el("div", "", e.message || "Something went wrong."));
      this.body.prepend(box);
    }
  }

  _decodeUi(text) {
    this.body.textContent = "";
    this.foot.textContent = "";
    const t = this._el("div", "lead");
    const sp = this._el("span", "spin");
    const label = this._el("span", "", text);
    t.append(sp, label);
    const prog = this._el("div", "scw-prog");
    const bar = this._el("i");
    prog.append(bar);
    this.body.append(t, prog);
    this.foot.append(this._btn("Cancel", "btn alt", () => (this._decodeCancel = true) && this._go("pick")));
    this._decodeCancel = false;
    return {
      set: (s, p) => {
        label.textContent = s;
        bar.style.width = `${Math.round(p * 100)}%`;
      },
    };
  }

  // ---- decode ----------------------------------------------------------------------------------------------------------------------------
  async _s_decode() {
    /* the screen is built by _decodeUi */
  }

  async _decode(frames, w, h) {
    this._go("decode");
    const msg = this._decodeUi("Finding the lights in the pictures…");
    await sleep(30);
    if (this.closed) return;
    try {
      const S = this.S;
      msg.set("Finding the lights in the pictures…", 0.2);
      await sleep(20);
      const res = this.usedMode === "colour" ? S.decodeColourScan(frames, this.N, w, h) : S.decodeScan(frames, this.N, w, h);
      msg.set("Placing them…", 0.8);
      await sleep(20);
      if (this.closed || this._decodeCancel) return;
      const ranges = this._ranges();
      const parts = ranges.map((r) => S.fillMissing(res.points.slice(r.first - this.base, r.first - this.base + r.count).map((p) => (p ? [p[0], p[1]] : null))));
      const total = parts.reduce((a, p) => a + p.x.length, 0);
      const cat = { x: new Float64Array(total), y: new Float64Array(total), est: new Uint8Array(total), found: 0 };
      let o = 0;
      for (const p of parts) {
        cat.x.set(p.x, o);
        cat.y.set(p.y, o);
        cat.est.set(p.est, o);
        cat.found += p.found;
        o += p.x.length;
      }
      if (cat.found < 3) {
        const why = res.blobs ? `It saw ${res.blobs} bright spots but could read the code of only ${res.found}.` : "It didn't see any lights.";
        throw new Error(`${why} ${this.usedMode === "colour" ? "Dim the room lights more, make sure no coloured lamps shine at the camera," : "Try a darker room,"} hold the phone steadier, and make sure the light is in view.`);
      }
      if (this.multiAngles) {
        this._addAngle(res, w, h);
        return this._go("angles");
      }
      const world = S.toWorld(cat);
      this._remerge = true;
      this.scans[this.view] = { world, res, found: cat.found, total, ranges, blobs: res.blobs, rejected: res.rejected, orient: this._orientAtScan };
      this._go("review");
    } catch (e) {
      if (this.closed) return;
      this._failScan(e.message || "Couldn't read the scan.", true);
    }
  }

  // ---- several angles -----------------------------------------------------------------------------------------------------------------
  // Each angle is an ordinary colour scan; its decoded pixel positions are kept (this.mv.views) and light-show-studio-multiview.js solves
  // them together once there are two or more. The screen between scans shows how much is covered, where it thinks you stood, and where to go next.
  _backStep(fromFail) {
    if (this.multiAngles) return this.mv?.views.length ? "angles" : "pick";
    return fromFail ? (this.scans.front ? "review" : "pick") : this.view === "side" ? "review" : "pick";
  }

  // motion access for the phone's tilt (levels the model); iOS shows a prompt, and only in answer to a tap. Never needed.
  async _askOrientation() {
    if (this._oriOn || typeof window.DeviceOrientationEvent === "undefined") return;
    try {
      const D = window.DeviceOrientationEvent;
      if (typeof D.requestPermission === "function" && (await D.requestPermission()) !== "granted") return;
      this._onOri = (e) => {
        if (e.beta != null && e.gamma != null) this.orient = { alpha: e.alpha || 0, beta: e.beta, gamma: e.gamma };
      };
      window.addEventListener("deviceorientation", this._onOri);
      this._oriOn = true;
    } catch (e) {
      /* refused or not supported: the scan works without it */
    }
  }

  // keep this scan's decoded points as one more view
  _addAngle(res, w, h) {
    const points = new Map();
    res.points.forEach((p, i) => p && points.set(i, [p[0], p[1]]));
    const view = { width: w, height: h, points, orientation: this._orientAtScan || undefined, found: res.found };
    const mv = this.mv;
    if (mv.redo >= 0) {
      mv.views[mv.redo] = view;
      mv.redo = -1;
    } else mv.views.push(view);
    mv.result = null;
  }

  // a single front scan becomes the first angle of a several-angle scan
  async _toMulti() {
    const f = this.scans.front;
    await this._askOrientation();
    const points = new Map();
    f.res.points.forEach((p, i) => p && points.set(i, [p[0], p[1]]));
    this.multiAngles = true;
    this.mv = { views: [{ width: f.res.w, height: f.res.h, points, orientation: f.orient || undefined, found: f.found }], redo: -1, entry: this.entry, part: this.part, result: null };
    this.scans = {};
    this.pos = null;
    this._go("aim");
  }

  // the angle of each placed camera round the lights, in degrees: 0 = the first camera, negative = to its left
  _azimuths(res) {
    return res.views.map((v) => (v.ok ? (Math.atan2(v.center[0] - 0.5, v.center[2] - 0.5) * 180) / Math.PI : null));
  }

  // where to go for angle number k+1
  _angleHint(k) {
    const r = this.mv?.result;
    if (r && r.ok && r.views.length >= k) {
      if (r.views.slice(0, k).some((v) => !v.ok)) return "The last angle didn't join up with the others. Stand about 20 degrees closer to your earlier spots, where the same lights are still in view, and scan again.";
      const az = this._azimuths(r).filter((a) => a !== null);
      const left = -Math.min(0, ...az), right = Math.max(0, ...az);
      const goLeft = left <= right, target = Math.min(150, Math.round(((goLeft ? left : right) + 45) / 5) * 5);
      return `Move to about ${target} degrees to the ${goLeft ? "left" : "right"} of your first spot (about 45 degrees past the furthest one so far), keep the lights in view, and scan again.`;
    }
    return k === 1 ? "Move about 45 degrees to the left of where you stood, keep the lights in view, and scan again." : k === 2 ? "Now go back to the front and move about 45 degrees to the right, then scan again." : "Move about 45 degrees further round, on whichever side you've covered less, and scan again.";
  }

  // pixel indexes (of the scan's code) that belong to the runs being scanned
  _scanIdx() {
    const out = [];
    for (const r of this._ranges()) for (let i = 0; i < r.count; i++) out.push(r.first - this.base + i);
    return out;
  }

  // solve the views collected so far (cached until another angle is added); the screen gets a moment to draw first
  async _solveViews() {
    const mv = this.mv;
    if (mv.result) return mv.result;
    await sleep(40);
    const res = this.mods.multiview.solveMultiView(mv.views, { n: this.N, fovDeg: 65 });
    mv.result = res;
    return res;
  }

  _s_angles() {
    const mv = this.mv, M = this.mods.multiview, K = mv.views.length;
    if (!K) return this._go("pick");
    this.title.textContent = `${K} angle${K === 1 ? "" : "s"} scanned`;
    const b = this.body;
    const cov = M.coverage(mv.views, this.N), idx = this._scanIdx();
    let two = 0, one = 0;
    for (const i of idx) {
      if (cov.seen[i] >= 2) two++;
      if (cov.seen[i] >= 1) one++;
    }
    const stats = this._el("div", "scw-stats");
    const stat = (n, t, bad) => {
      const s = this._el("div", "scw-stat" + (bad ? " bad" : ""));
      s.append(this._el("b", "", String(n)), this._el("span", "", t));
      stats.append(s);
    };
    const total = idx.length;
    stat(two, `of ${total} bulbs seen in 2+ views`, K > 1 && two < total * 0.5);
    stat(one, "seen in at least one view");
    stat(K, "angles so far");
    b.append(stats);
    b.append(this._el("p", "lead", K > 1 ? `${two} of ${total} bulbs seen in 2+ views. Bulbs seen from two angles or more get a true 3D place; the others are placed along the string.` : "The first angle is done. Scan again from a different spot so each bulb is seen from at least two."));
    const box = this._el("div", "box");
    box.setAttribute("role", "status");
    const guide = this._el("strong", "", K >= 6 ? "That's plenty of angles. Tap Finish." : this._angleHint(K));
    box.append(guide);
    b.append(box);
    const sk = this._el("canvas", "scw-sketch");
    sk.width = 640;
    sk.height = 420;
    sk.setAttribute("role", "img");
    sk.setAttribute("aria-label", "Top-down sketch of the lights and where you stood for each angle");
    sk.hidden = K < 2;
    const note = this._el("div", "muted", K < 2 ? "A top-down sketch of where you stood appears after the second angle." : "Working out where you stood…");
    b.append(sk, note);
    const err = this._el("div", "serr");
    err.hidden = true;
    err.setAttribute("role", "alert");
    b.append(err);
    const add = this._btn("Add another angle", "btn", () => {
      mv.redo = -1;
      this._go("aim");
    }, ICON.cam);
    add.disabled = K >= 6;
    const fin = this._btn("Finish", "btn", () => this._finishMulti());
    fin.disabled = K < 2;
    this.foot.append(
      this._btn("Cancel", "btn alt", () => this.close()),
      this._btn(K > 1 ? "Redo the last angle" : "Redo this angle", "btn alt", () => {
        mv.redo = K - 1;
        this._go("aim");
      }),
      fin,
      add,
    );
    if (K >= 2) {
      this._solveViews().then((res) => {
        if (this.closed || this.step !== "angles" || this.mv !== mv) return;
        if (!res.ok) {
          note.textContent = "";
          err.textContent = res.error;
          err.hidden = false;
          sk.hidden = true;
          return;
        }
        const placed = res.views.filter((v) => v.ok).length;
        const lost = res.views.map((v, i) => (v.ok ? 0 : i + 1)).filter(Boolean);
        note.textContent = lost.length
          ? `Angle ${lost.join(", ")} couldn't be joined to the others (too few of the same lights in view). Redo it from closer to the earlier spots.`
          : `Where the app thinks you stood (top-down, the first angle at the bottom): ${placed} of ${K} placed, picture error about ${res.rms.toFixed(1)} px.`;
        if (lost.length) guide.textContent = this._angleHint(K);
        else if (K < 6) guide.textContent = this._angleHint(K);
        this._drawSketch(sk, res);
      });
    }
  }

  // top-down map: lights (x across, z down the page, the first camera at the bottom) and a numbered marker for every placed camera
  _drawSketch(cv, res) {
    const g = cv.getContext("2d"), W = cv.width, H = cv.height;
    const css = getComputedStyle(this.root);
    const acc = css.getPropertyValue("--lss-acc").trim() || "#4aa3ff", mut = css.getPropertyValue("--lss-mut").trim() || "#888", txt = css.getPropertyValue("--lss-text").trim() || "#ddd";
    g.clearRect(0, 0, W, H);
    const pts = [];
    for (const i of this._scanIdx()) if (!res.est[i]) pts.push([res.pos[3 * i], res.pos[3 * i + 2], i]);
    const cams = res.views.map((v, k) => (v.ok ? { x: v.center[0], z: v.center[2], fx: v.forward[0], fz: v.forward[2], k: k + 1 } : null)).filter(Boolean);
    let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
    for (const [x, z] of pts) (x0 = Math.min(x0, x)), (x1 = Math.max(x1, x)), (z0 = Math.min(z0, z)), (z1 = Math.max(z1, z));
    for (const c of cams) (x0 = Math.min(x0, c.x)), (x1 = Math.max(x1, c.x)), (z0 = Math.min(z0, c.z)), (z1 = Math.max(z1, c.z));
    if (!Number.isFinite(x0)) return;
    const pad = 34, sc = Math.min((W - 2 * pad) / Math.max(x1 - x0, 1e-6), (H - 2 * pad) / Math.max(z1 - z0, 1e-6));
    const ox = (W - (x1 - x0) * sc) / 2, oy = (H - (z1 - z0) * sc) / 2;
    const X = (x) => ox + (x - x0) * sc, Y = (z) => oy + (z - z0) * sc;
    g.globalAlpha = 0.85;
    for (const [x, z, i] of pts) {
      const r = this._ranges().find((q) => i >= q.first - this.base && i < q.first - this.base + q.count);
      const rgb = r ? this.o.colourOf(r.run.id) : [160, 160, 160];
      g.fillStyle = `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
      g.fillRect(X(x) - 1.5, Y(z) - 1.5, 3, 3);
    }
    g.globalAlpha = 1;
    g.font = "bold 15px system-ui, sans-serif";
    g.textAlign = "center";
    g.textBaseline = "middle";
    for (const c of cams) {
      const px = X(c.x), py = Y(c.z), a = Math.atan2(c.fz, c.fx);
      g.strokeStyle = mut;
      g.lineWidth = 2;
      g.beginPath();
      g.moveTo(px, py);
      g.lineTo(px + Math.cos(a) * 26, py + Math.sin(a) * 26);
      g.stroke();
      g.fillStyle = acc;
      g.beginPath();
      g.arc(px, py, 13, 0, 6.2832);
      g.fill();
      g.fillStyle = "#fff";
      g.fillText(String(c.k), px, py + 1);
    }
    g.fillStyle = txt;
    g.font = "12px system-ui, sans-serif";
    g.textAlign = "left";
    g.fillText("Top-down: lights as dots, you as numbered circles", 10, H - 10);
  }

  // solve everything and show the 3D result in the usual review screen
  async _finishMulti() {
    const mv = this.mv;
    this._go("decode");
    const msg = this._decodeUi("Working out where every light is in 3D…");
    msg.set("Working out where every light is in 3D…", 0.4);
    try {
      const res = await this._solveViews();
      if (this.closed || this._decodeCancel) return;
      if (!res.ok) throw new Error(res.error);
      const ranges = this._ranges();
      const total = ranges.reduce((a, r) => a + r.count, 0);
      const pos = new Float32Array(total * 3), est = new Uint8Array(total);
      let o = 0, found = 0;
      for (const r of ranges) {
        for (let i = 0; i < r.count; i++) {
          const src = r.first - this.base + i;
          pos[(o + i) * 3] = res.pos[src * 3];
          pos[(o + i) * 3 + 1] = res.pos[src * 3 + 1];
          pos[(o + i) * 3 + 2] = res.pos[src * 3 + 2];
          est[o + i] = res.est[src];
          if (!res.est[src]) found++;
        }
        o += r.count;
      }
      if (found < 3) throw new Error("Too few lights were seen from two angles. Scan again with more of the light in view each time.");
      this.pos = pos;
      this.est = est;
      this.fitInfo = null;
      this._remerge = false;
      this.edited = false;
      this.scans = { front: { multi: true, found, total, ranges, solve: res, angles: mv.views.length } };
      this._go("review");
    } catch (e) {
      if (this.closed) return;
      this._go("angles");
      const box = this._el("div", "box mp-banner");
      box.setAttribute("role", "alert");
      box.append(this._el("strong", "", "Couldn't work out the 3D positions"), this._el("div", "", e.message || "Something went wrong."));
      this.body.prepend(box);
    }
  }

  // ---- review ------------------------------------------------------------------------------------------------------------------------------
  _merge() {
    const f = this.scans.front, s = this.scans.side;
    const m = this.S.mergeViews(f.world, s ? s.world : null, { flipZ: this.flipZ });
    this.pos = m.pos;
    this.est = Uint8Array.from(f.world.est);
    this.fitInfo = m;
  }

  _dropViewer() {
    this.viewer?.destroy();
    this.viewer = null;
  }

  _s_review() {
    const f = this.scans.front;
    if (!f) return this._go("pick");
    if (!f.multi && (!this.pos || this._remerge)) {
      this._merge();
      this._remerge = false;
    }
    const hasSide = !!this.scans.side || !!f.multi; // (a several-angle scan has depth from the start)
    this.title.textContent = "Check the result";
    const b = this.body;
    const found = f.found, total = f.total;
    const stats = this._el("div", "scw-stats");
    const stat = (n, t, bad) => {
      const s = this._el("div", "scw-stat" + (bad ? " bad" : ""));
      s.append(this._el("b", "", String(n)), this._el("span", "", t));
      stats.append(s);
    };
    if (f.glow) {
      stat(found, `spots found of ${total}`, found < total * 0.6);
      stat(f.guessed, "pixels guessed (between skipped spots)", f.guessed > f.px * 0.3);
    } else {
      stat(found, `found of ${total}`, found < total * 0.6);
      stat(total - found, f.multi ? "placed along the string (seen from fewer than 2 angles)" : "guessed (no camera view)", total - found > total * 0.4);
    }
    if (f.multi) stat("3D", `${f.angles} angles joined, picture error about ${f.solve.rms.toFixed(1)} px`);
    else stat(hasSide ? "3D" : "Flat", hasSide ? "front and side joined" : "front only: no depth yet");
    b.append(stats);
    if (found < total * 0.6) {
      const w = this._el("div", "box mp-banner");
      w.append(this._el("strong", "", f.glow ? "Quite a few spots weren't followed" : "Quite a few lights weren't seen"));
      w.append(this._el("div", "muted", f.glow
        ? "Try again in a darker room with all the glowing surfaces in view, or fix the dots below by hand."
        : "Try again in a darker room with the whole light in view, or fix the dots below by hand."));
      b.append(w);
      if (!f.glow) b.append(this._glowOffer("If these lights are hidden (under cabinets, behind covers), the camera only sees a glow, not dots. The Glow scan follows that glow instead."));
    }
    if (f.dropped?.length) {
      const w = this._el("div", "box mp-banner");
      w.setAttribute("role", "alert");
      w.append(this._el("strong", "", "Not saved: too few spots"), this._el("div", "muted", `${f.dropped.join(", ")} will keep the places they have now. Scan again, aiming at the surfaces those lights shine on.`));
      b.append(w);
    }
    const wrap = this._el("div", "scw-vw");
    const cv = this._el("canvas");
    cv.setAttribute("role", "img");
    cv.setAttribute("aria-label", "The scanned lights. Drag a dot to move it.");
    const top = this._el("div", "vt");
    const seg = this._el("div", "seg");
    seg.setAttribute("role", "radiogroup");
    seg.setAttribute("aria-label", "View");
    const reset = this._el("button", "ibv");
    reset.type = "button";
    reset.innerHTML = svg(ICON.reset, 20);
    reset.setAttribute("aria-label", "Reset the view");
    reset.title = "Reset the view";
    top.append(seg, reset);
    wrap.append(cv, top);
    const hint = this._el("div", "muted");
    hint.setAttribute("aria-live", "polite");
    b.append(wrap, hint);
    if (f.glow) b.append(this._chips(f));
    // viewer data
    const n = this.pos.length / 3;
    const base = new Uint8ClampedArray(n * 3);
    const groups = [];
    let o = 0;
    f.ranges.forEach((r) => {
      const rgb = this.o.colourOf(r.run.id);
      for (let i = 0; i < r.count; i++) base.set(rgb, (o + i) * 3);
      groups.push({ start: o, len: r.count, rgb });
      o += r.count;
    });
    const flags = Uint8Array.from(this.est);
    const V = this.mods.Viewer3D;
    const v = (this.viewer = new V(cv, {
      interactive: true,
      dotR: n > 600 ? 0.0055 : 0.008,
      onEdit: (hit, pos) => {
        flags[hit.i] = 0;
        this.est[hit.i] = 0;
        this.edited = true;
      },
      onEditEnd: (hit) => {
        this._syncWorldFromPos(hit.i);
      },
      onSelect: () => {},
      onMode: (m) => draw(m),
    }));
    v.setDots({ n, pos: this.pos, base, groups, flags });
    v.editable = "points";
    const modes = [["front", "Front"], ["3d", "3D"], ...(hasSide ? [["side", "Side"]] : [])];
    const draw = (cur) => {
      seg.textContent = "";
      for (const [m, t] of modes) {
        const bt = this._btn(t, "segb", () => v.setMode(m));
        bt.setAttribute("role", "radio");
        bt.setAttribute("aria-checked", String(m === cur));
        seg.append(bt);
      }
      hint.textContent = cur === "3d"
        ? "Drag to look around, pinch or scroll to zoom, double tap to reset. Switch to Front to move dots."
        : `Drag a dot that looks wrong to where it belongs${hasSide ? "" : ""}. Dots ringed in orange were guessed because the camera didn't see them.`;
    };
    reset.onclick = () => v.reset();
    v.setMode("front", false);
    draw("front");
    // actions
    if (f.multi) {
      b.append(this._el("div", "muted", "Orange-ringed dots were seen from fewer than two angles, so they sit along the string between the ones that were. Switch to 3D and look round to check the shape."));
    } else if (!hasSide) {
      b.append(this._el("div", "muted", "Want depth too? Do a second scan from the side, about 90 degrees round to the right of the lights."));
    } else {
      const fl = this._btn("Flip the depth (front/back)", "btn alt sm", () => {
        this.flipZ = !this.flipZ;
        this._remerge = true;
        this._go("review");
      });
      b.append(fl);
    }
    const err = this._el("div", "serr");
    err.hidden = true;
    err.setAttribute("role", "alert");
    b.append(err);
    this.foot.append(
      this._btn("Scan again", "btn alt", () => {
        if (f.multi) return this._go("angles"); // add or redo angles, then finish again
        delete this.scans[this.view === "side" ? "side" : "front"];
        this.pos = null;
        this._go("aim");
      }),
    );
    if (!hasSide) {
      this.foot.append(this._btn("Add a side scan", "btn alt", () => {
        this.view = "side";
        this._go("aim");
      }));
      if (!f.glow && f.res?.points && this.mods.multiview) this.foot.append(this._btn("Scan from several angles", "btn alt", () => this._toMulti()));
    }
    const save = this._btn("Save positions", "btn", async () => {
      save.disabled = true;
      err.hidden = true;
      const msg = await this._save();
      save.disabled = false;
      if (msg) {
        err.textContent = msg;
        err.hidden = false;
      }
    }, ICON.save);
    this.foot.append(save);
    if (this.view === "side" && !hasSide) this.view = "front";
  }

  // glow scan: every spot of every run with how sure it was
  _chips(f) {
    const box = this._el("div");
    box.append(this._el("h3", "", "How sure each spot was"));
    for (const info of f.infos) {
      const card = this._el("div", "scw-card");
      const okN = info.sol.anchors.filter((a) => a.status === "ok").length;
      card.append(this._el("strong", "", `${info.run.name}: ${okN} of ${info.plan.anchors.length} spots used`));
      const row = this._el("div", "scw-chips");
      info.sol.anchors.forEach((a, k) => {
        const pl = info.plan.anchors[k];
        const chip = this._el("span", "scw-chip" + (a.status !== "ok" ? " bad" : a.manual ? " manual" : ""), a.status !== "ok" ? "!" : String(k + 1));
        const what = a.status === "ok" ? (a.manual ? "placed by you" : `${Math.round((a.conf || 0) * 100)}% sure`) : a.skipped ? "skipped" : a.status === "outlier" ? "looked wrong, not used" : a.status === "low" ? "too faint, not used" : "not found";
        const label = `Spot ${k + 1}, pixels ${pl.start + 1} to ${pl.start + pl.width}: ${what}`;
        chip.title = label;
        chip.setAttribute("role", "img");
        chip.setAttribute("aria-label", label);
        if (a.status === "ok" && !a.manual) chip.style.opacity = String(0.45 + 0.55 * (a.conf || 0));
        row.append(chip);
      });
      card.append(row);
      box.append(card);
    }
    box.append(this._el("div", "muted", "Green spots were found, blue ones you placed. Orange ! spots were skipped or looked wrong; pixels near them are guessed (ringed in orange above). Drag dots in Front view to fix them."));
    return box;
  }

  // an edited dot (front plane) updates the front scan's own numbers so a later side scan keeps the fix
  _syncWorldFromPos(i) {
    const f = this.scans.front, m = this.fitInfo;
    if (!f || !m || this.scans.side) return;
    const w = f.world, k = m.k, c = m.c;
    w.x[i] = (this.pos[i * 3] - 0.5) / k + c[0];
    w.y[i] = (this.pos[i * 3 + 1] - 0.5) / k + c[1];
    w.est[i] = 0;
  }

  async _save() {
    const f = this.scans.front;
    const out = [];
    let o = 0;
    for (const r of f.ranges) {
      const arr = new Array(r.count * 3);
      for (let i = 0; i < r.count; i++) {
        for (let k = 0; k < 3; k++) arr[i * 3 + k] = Math.max(0, Math.min(1000, Math.round(this.pos[(o + i) * 3 + k] * 1000)));
      }
      out.push({ id: r.run.id, positions: arr });
      o += r.count;
    }
    const msg = await this.o.onSave({ entry_id: this.entry, runs: out, depth: !!this.scans.side || !!f.multi });
    if (msg) return msg;
    this.close();
    return "";
  }

  // ---- views for the second scan -----------------------------------------------------------------------------------------------------------
  // after a side scan the review merges both views
  _afterDecode() {
    this._remerge = true;
  }

  async close() {
    if (this.closed) return;
    this.closed = true;
    this._aborted = "Cancelled.";
    document.removeEventListener("visibilitychange", this._onVis);
    window.removeEventListener("pagehide", this._onHide);
    if (this._onOri) window.removeEventListener("deviceorientation", this._onOri);
    this._dropViewer();
    this._closeCamera();
    this.root.remove();
    await this._stopScan(true);
    this.o.onClose?.();
  }

  // the panel calls this when the user leaves the Map tab
  destroy() {
    return this.close();
  }
}
