// Govee C9 card: every bulb of a string, live, and paintable. Ships with the govee_light_show integration.
//
//   type: custom:govee-light-show-card
//   entity: light.govee_light_show          # the whole-string light
//   title: Porch                    # optional
//   columns: 25                     # optional: wrap into rows of this many bulbs (default: one row that wraps to fit)
//   layout: [[0, 0], [10, 0], ...]  # optional: x, y of each bulb (any units), e.g. your xLights positions
//   colors: ["#ff0000", ...]        # optional: the paint swatches
//   patterns:                       # optional: replaces the Patterns list (name: colours repeated along the string)
//     Team colours: ["#002244", "#69be28"]

const COLORS = ["#ff0000", "#ff5000", "#ffc800", "#00ff00", "#00ffc8", "#0040ff", "#8200ff", "#ff1478",
  "#ffb43c", "#ffffff"];

// one bulb per colour, repeated along the string; a colour listed twice makes a wider band
const PATTERNS = {
  Christmas: ["#ff0000", "#00ff00"],
  "Classic multicolour": ["#ff0000", "#00ff00", "#0040ff", "#ff5000", "#ff1478"],
  "Candy cane": ["#ff0000", "#ff0000", "#ffffff", "#ffffff"],
  Hanukkah: ["#0040ff", "#ffffff"],
  "New Year": ["#ffc800", "#ffffff", "#ffc800", "#000000"],
  "Valentine's": ["#ff0000", "#ff1478", "#ffffff"],
  "St. Patrick's": ["#00ff00", "#ffffff", "#ff8c00"],
  Easter: ["#ff78aa", "#78c8ff", "#ffdc5a", "#96ff8c", "#be82ff"],
  "July 4th": ["#ff0000", "#ffffff", "#0040ff"],
  Halloween: ["#ff5000", "#8200ff"],
  Thanksgiving: ["#ff5000", "#ff8c00", "#b41e00"],
  "Warm white": ["#ffb43c"],
  Rainbow: ["#ff0000", "#ff5000", "#ffc800", "#00ff00", "#0040ff", "#8200ff"],
};

class GoveeC9Card extends HTMLElement {
  _pending = {}; // bulb number -> colour, waiting to be sent
  _overlay = {}; // optimistic paints shown until a live frame confirms them

  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find((id) => {
      const a = hass.states[id].attributes;
      return id.startsWith("light.") && a.bulbs && (a.effect_list || []).includes("Light show");
    });
    return { entity: entity || "" };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "entity", required: true, selector: { entity: { domain: "light", integration: "govee_light_show" } } },
        { name: "title", selector: { text: {} } },
        { name: "columns", selector: { number: { min: 0, max: 255, mode: "box" } } },
        { name: "layout", selector: { object: {} } },
        { name: "colors", selector: { object: {} } },
        { name: "patterns", selector: { object: {} } },
      ],
    };
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Pick a Govee C9 string light");
    const old = this._config;
    this._config = { columns: 0, ...config };
    this._brush = this._brush === undefined ? this._colors()[0] : this._brush;
    this._retryAt = 0;
    if (old && old.entity !== config.entity) this._unsubscribe();
    this._built = false;
    if (this._hass) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._updateStatus();
    this._subscribe();
  }

  getCardSize() {
    return 3;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  connectedCallback() {
    this._retryAt = 0;
    if (this._hass) this._subscribe();
  }

  disconnectedCallback() {
    this._unsubscribe();
    clearTimeout(this._sendTimer);
    if (Object.keys(this._pending).length) this._send();
  }

  // --- live bulbs --------------------------------------------------------
  async _subscribe() {
    if (this._unsub || this._subscribing || !this.isConnected || !this._config?.entity || !this._hass) return;
    if (Date.now() < (this._retryAt || 0)) return; // a failed subscribe waits 10 s (or a new setConfig / reconnect)
    this._subscribing = true;
    const entity = this._config.entity;
    try {
      const unsub = await this._hass.connection.subscribeMessage((m) => this._onFrame(m), {
        type: "govee_light_show/subscribe",
        entity_id: entity,
      });
      if (!this.isConnected || this._config?.entity !== entity) unsub(); // gone or re-pointed while we waited
      else {
        this._unsub = unsub;
        this._error("");
      }
    } catch (err) {
      this._retryAt = Date.now() + 10000;
      this._error(this._msg(err, "Can't reach the string. Trying again soon."));
    }
    this._subscribing = false;
    if (!this._unsub && this.isConnected && this._config?.entity !== entity) this._subscribe();
  }

  _msg(err, fallback) {
    return (typeof err === "string" ? err : err?.message) || fallback;
  }

  _unsubscribe() {
    if (this._unsub) {
      try {
        this._unsub();
      } catch (e) {
        /* connection already closed */
      }
    }
    this._unsub = null;
  }

  _onFrame(m) {
    if (m.bulbs !== this._count) {
      this._count = m.bulbs;
      this._buildBulbs();
    }
    this._live = m;
    // optimistic paint stays until a live frame after the server confirmed it
    const now = Date.now();
    for (const k of Object.keys(this._overlay)) {
      const o = this._overlay[k];
      if (o.doneAt && now - o.doneAt > 250 && !(k in this._pending)) delete this._overlay[k];
    }
    for (let i = 0; i < this._count; i++) {
      const o = this._overlay[i + 1];
      this._paintBulb(i, o ? o.c : m.on ? "#" + m.frame.substr(i * 6, 6) : null);
    }
    this._updateStatus();
  }

  _paintBulb(i, colour) {
    const el = this._bulbs?.[i];
    if (!el) return;
    const lit = colour && colour !== "#000000";
    el.style.background = lit ? colour : "";
    el.style.boxShadow = lit ? `0 0 calc(var(--b, 18px) * 0.55) calc(var(--b, 18px) * 0.1) ${colour}` : "";
    el.classList.toggle("off", !lit);
  }

  // --- painting ----------------------------------------------------------
  _canPaint() {
    return this._hass?.states[this._config.entity]?.state !== "unavailable";
  }

  _set(n, colour) {
    this._pending[n] = colour;
    this._overlay[n] = { c: colour, doneAt: 0 };
    this._paintBulb(n - 1, colour);
  }

  _paint(i) {
    if (!this._canPaint()) return;
    const last = this._lastPainted;
    if (i === last) return;
    this._lastPainted = i;
    // a fast drag skips bulbs between pointer events: fill in short gaps along the string
    const from = last !== null && Math.abs(i - last) <= 12 ? last : i;
    for (let j = Math.min(from, i); j <= Math.max(from, i); j++) this._set(j + 1, this._brush);
    clearTimeout(this._sendTimer);
    this._sendTimer = setTimeout(() => this._send(), 120);
  }

  async _send() {
    const b = this._pending; // paints made while this call is in flight go into a fresh map
    this._pending = {};
    const keys = Object.keys(b);
    if (!keys.length) return;
    try {
      await this._hass.callWS({ type: "govee_light_show/set_bulbs", entity_id: this._config.entity, bulbs: b });
      const now = Date.now();
      keys.forEach((k) => this._overlay[k] && !(k in this._pending) && (this._overlay[k].doneAt = now));
    } catch (err) {
      keys.forEach((k) => !(k in this._pending) && delete this._overlay[k]);
      this._error(this._msg(err, "Couldn't paint the string."));
      if (this._live) this._onFrame(this._live);
    }
  }

  _fill(colour) {
    if (!this._canPaint()) return;
    for (let i = 0; i < this._count; i++) this._set(i + 1, colour);
    this._send();
  }

  _fillPattern(colours) {
    if (!Array.isArray(colours) || !colours.length || !this._canPaint()) return;
    for (let i = 0; i < this._count; i++) {
      const c = colours[i % colours.length];
      this._set(i + 1, c && c !== "#000000" ? c : null);
    }
    this._send();
  }

  _colors() {
    return Array.isArray(this._config?.colors) && this._config.colors.length ? this._config.colors : COLORS;
  }

  async _lightShow() {
    try {
      await this._hass.callService("light", "turn_on", { entity_id: this._config.entity, effect: "Light show" });
      this._error("");
    } catch (err) {
      this._error(this._msg(err, "Couldn't hand the string back to the show."));
    }
  }

  _showActive() {
    const s = this._hass?.states[this._config.entity];
    return !!(this._live?.show || (s?.state === "on" && s.attributes.effect === "Light show"));
  }

  // --- layout ------------------------------------------------------------
  _build() {
    if (!this._config) return;
    this._built = true;
    this._paintMode = false;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 12px 16px 16px; }
        .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; }
        .title { font-size: 16px; font-weight: 500; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .status { font-size: 12px; color: var(--secondary-text-color); flex: none; }
        .status.bad { color: var(--error-color); font-weight: 500; }
        /* vertical swipes scroll the page; painting needs a press-and-hold (or Paint mode) on touch */
        .strip { position: relative; touch-action: pan-y; user-select: none; -webkit-user-select: none;
                 -webkit-touch-callout: none; padding: 8px; border-radius: 12px; background: #0b0b10; }
        .strip.paintmode { touch-action: none; outline: 2px solid var(--primary-color); outline-offset: 1px; }
        .strip.dis { opacity: 0.5; }
        /* bulbs scale with the card: grid cells shrink to fit, capped at 22px */
        .strip.grid, .strip.flow { display: grid; gap: 5px 2%; justify-items: center; align-items: center; }
        .strip.flow { grid-template-columns: repeat(auto-fill, minmax(14px, 1fr)); gap: 5px; }
        .bulb { width: min(100%, 22px); aspect-ratio: 1; border-radius: 50%; cursor: pointer; box-sizing: border-box;
                transition: background 0.08s; --b: 18px; }
        .strip.dis .bulb { cursor: not-allowed; }
        .bulb.off { background: #26262e; box-shadow: inset 0 0 0 1px #3a3a44; }
        .strip.points { padding: 14px 12px; }
        ha-card, .strip { min-width: 0; max-width: 100%; box-sizing: border-box; }
        .area { position: relative; --b: 12px; }
        .strip.points .bulb { position: absolute; transform: translate(-50%, -50%); width: var(--b); height: var(--b);
                              --b: inherit; }
        .tools { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 12px; }
        .tools.dis { opacity: 0.5; pointer-events: none; }
        .sw { width: 28px; height: 28px; border-radius: 50%; border: 2px solid transparent; cursor: pointer;
              box-sizing: border-box; padding: 0; box-shadow: inset 0 0 0 1px #0003; }
        .sw.sel { border-color: var(--primary-text-color); box-shadow: 0 0 0 2px var(--card-background-color, #fff), inset 0 0 0 1px #0003; }
        .sw.offsw { background: #26262e; color: #aaa; font-size: 11px; }
        input[type=color] { width: 32px; height: 30px; border: 2px solid transparent; border-radius: 8px; background: none;
                            padding: 0; cursor: pointer; box-sizing: content-box; }
        input[type=color].sel { border-color: var(--primary-text-color); }
        .spacer { flex: 1; }
        .act { font: inherit; font-size: 13px; padding: 6px 10px; border-radius: 8px; cursor: pointer; min-height: 32px;
               border: 1px solid var(--divider-color); background: var(--card-background-color);
               color: var(--primary-text-color); }
        .act[aria-pressed=true] { background: var(--primary-color); border-color: var(--primary-color);
                                  color: var(--text-primary-color, #fff); }
        :focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
        .hint { font-size: 12px; color: var(--secondary-text-color); margin-top: 8px; }
        .hint.warn { color: var(--warning-color, #b36b00); }
        .err { color: var(--error-color); font-size: 12px; margin-top: 8px; }
        /* phones: 40px+ touch targets */
        @media (pointer: coarse), (max-width: 600px) {
          ha-card { padding: 12px; }
          .sw { width: 40px; height: 40px; }
          input[type=color] { width: 40px; height: 36px; }
          .act { min-height: 40px; padding: 8px 12px; font-size: 14px; }
          .tools { gap: 6px 8px; }
        }
      </style>
      <ha-card>
        <div class="head"><span class="title"></span><span class="status" role="status"></span></div>
        <div class="strip" role="group" aria-label="Bulbs"></div>
        <div class="hint warn" hidden></div>
        <div class="tools"></div>
        <div class="hint paintinfo"></div>
        <div class="err" role="alert" hidden></div>
      </ha-card>`;
    const root = this.shadowRoot;
    root.querySelector(".title").textContent =
      this._config.title || this._hass.states[this._config.entity]?.attributes.friendly_name || this._config.entity;
    this._strip = root.querySelector(".strip");
    this._buildTools(root.querySelector(".tools"));
    this._count = this._count || this._hass.states[this._config.entity]?.attributes.bulbs || 50;
    this._buildBulbs();
    this._wirePointer(root);
    this._updateStatus();
  }

  // mouse paints at once; touch/pen paints after a ~250 ms press-and-hold (or in Paint mode), so swipes still scroll
  _wirePointer(root) {
    const strip = this._strip;
    const at = (e) => {
      const el = root.elementFromPoint(e.clientX, e.clientY);
      return el && el.dataset.i !== undefined ? Number(el.dataset.i) : null;
    };
    const begin = (e, i) => {
      this._painting = true;
      this._lastPainted = null;
      try {
        strip.setPointerCapture(e.pointerId);
      } catch (err) {
        /* pointer already gone */
      }
      this._paint(i);
    };
    let hold = null, down = null;
    const cancelHold = () => {
      clearTimeout(hold);
      hold = null;
      down = null;
    };
    strip.addEventListener("pointerdown", (e) => {
      if (!this._canPaint() || (e.pointerType === "mouse" && e.button !== 0)) return;
      const i = at(e);
      if (i === null) return;
      if (e.pointerType === "mouse" || this._paintMode) return begin(e, i);
      down = { x: e.clientX, y: e.clientY };
      hold = setTimeout(() => {
        hold = null;
        navigator.vibrate?.(8);
        begin(e, at({ clientX: down.x, clientY: down.y }) ?? i);
      }, 250);
    });
    strip.addEventListener("pointermove", (e) => {
      if (hold && down && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 8) cancelHold(); // it's a scroll
      if (!this._painting) return;
      const i = at(e);
      if (i !== null) this._paint(i);
    });
    // once painting has started, a finger drag must not scroll the page
    strip.addEventListener("touchmove", (e) => this._painting && e.cancelable && e.preventDefault(), { passive: false });
    strip.addEventListener("contextmenu", (e) => e.pointerType !== "mouse" && e.preventDefault());
    const end = () => {
      cancelHold();
      this._painting = false;
    };
    strip.addEventListener("pointerup", end);
    strip.addEventListener("pointercancel", end);
  }

  _buildTools(tools) {
    this._tools = tools;
    const select = (el, colour) => {
      this._brush = colour;
      tools.querySelectorAll(".sw, input").forEach((s) => {
        s.classList.remove("sel");
        s.setAttribute?.("aria-pressed", "false");
      });
      el.classList.add("sel");
      el.setAttribute("aria-pressed", "true");
    };
    for (const c of this._colors()) {
      const b = document.createElement("button");
      b.className = "sw" + (c === this._brush ? " sel" : "");
      b.style.background = c;
      b.title = c;
      b.setAttribute("aria-label", `Paint with ${c}`);
      b.setAttribute("aria-pressed", String(c === this._brush));
      b.onclick = () => select(b, c);
      tools.append(b);
    }
    const off = document.createElement("button");
    off.className = "sw offsw" + (this._brush === null ? " sel" : "");
    off.textContent = "off";
    off.title = "Turn bulbs off";
    off.setAttribute("aria-label", "Paint with off (turn bulbs off)");
    off.setAttribute("aria-pressed", String(this._brush === null));
    off.onclick = () => select(off, null);
    tools.append(off);
    const pick = document.createElement("input");
    pick.type = "color";
    pick.value = "#ff8c00";
    pick.title = "Any colour";
    pick.setAttribute("aria-label", "Custom colour");
    pick.oninput = () => select(pick, pick.value);
    pick.onclick = () => select(pick, pick.value); // opening the picker also makes it the brush
    tools.append(pick);
    tools.append(Object.assign(document.createElement("span"), { className: "spacer" }));

    const mode = Object.assign(document.createElement("button"), { className: "act", textContent: "Paint" });
    mode.title = "Paint mode: touch and drag across bulbs to paint (the page won't scroll over them)";
    mode.setAttribute("aria-pressed", "false");
    mode.onclick = () => {
      this._paintMode = !this._paintMode;
      mode.setAttribute("aria-pressed", String(this._paintMode));
      this._strip.classList.toggle("paintmode", this._paintMode);
    };
    const fill = Object.assign(document.createElement("button"), { className: "act", textContent: "Paint all" });
    fill.title = "Paint every bulb with the selected colour";
    fill.onclick = () => this._fill(this._brush);
    const patterns = this._config.patterns || PATTERNS;
    const menu = Object.assign(document.createElement("select"), { className: "act", title: "Fill the string with a holiday pattern" });
    menu.setAttribute("aria-label", "Holiday patterns");
    menu.append(new Option("Holiday patterns…", ""));
    for (const name of Object.keys(patterns)) menu.append(new Option(name, name));
    menu.onchange = () => {
      if (menu.value) this._fillPattern(patterns[menu.value]);
      menu.value = "";
    };
    const show = Object.assign(document.createElement("button"), { className: "act", textContent: "Let the show control it" });
    show.title = "Hand the string back to xLights / EZPlayer / play_sequence";
    show.onclick = () => this._lightShow();
    tools.append(mode, menu, fill, show);
  }

  _buildBulbs() {
    const strip = this._strip;
    if (!strip) return;
    strip.innerHTML = "";
    this._bulbs = [];
    const { columns, layout } = this._config;
    const points = Array.isArray(layout) && layout.length ? layout : null;
    strip.className = "strip " + (points ? "points" : columns > 0 ? "grid" : "flow");
    strip.style.gridTemplateColumns = columns > 0 && !points ? `repeat(${columns}, minmax(0, 1fr))` : "";
    let place = null, parent = strip;
    if (points) {
      // positions as % of an area with the layout's own aspect ratio (a straight line still gets some height)
      const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
      const x0 = Math.min(...xs), y0 = Math.min(...ys);
      const w = Math.max(...xs) - x0 || 1, h = Math.max(...ys) - y0 || 0;
      parent = document.createElement("div");
      parent.className = "area";
      parent.style.aspectRatio = `${w} / ${Math.max(h, w / 30)}`;
      strip.append(parent);
      // bulb size follows the card width: 60% of the typical gap between neighbouring bulbs, 5-16px
      const near = points.map((p, i) => Math.min(...points.map((q, j) => (i === j ? Infinity : Math.hypot(p[0] - q[0], p[1] - q[1]) || Infinity))));
      const gap = near.filter(isFinite).sort((a, b) => a - b)[Math.floor(near.length / 2)] || w / 10;
      this._resize?.disconnect();
      this._resize = new ResizeObserver(([e]) => {
        const px = (gap * e.contentRect.width) / w;
        parent.style.setProperty("--b", `${Math.max(5, Math.min(16, px * 0.6))}px`);
      });
      this._resize.observe(parent);
      place = (el, p) => {
        el.style.left = `${((p[0] - x0) / w) * 100}%`;
        el.style.top = h ? `${((p[1] - y0) / h) * 100}%` : "50%";
      };
    }
    for (let i = 0; i < this._count; i++) {
      const el = document.createElement("div");
      el.className = "bulb off";
      el.dataset.i = i;
      el.title = `Bulb ${i + 1}`;
      if (place && points[i]) place(el, points[i]);
      else if (place) el.hidden = true;
      parent.append(el);
      this._bulbs.push(el);
    }
  }

  _updateStatus() {
    const root = this.shadowRoot;
    const st = root?.querySelector(".status");
    if (!st || !this._hass) return;
    const s = this._hass.states[this._config.entity];
    const gone = !s || s.state === "unavailable" || s.state === "unknown";
    if (!s) st.textContent = "Not found";
    else if (gone) st.textContent = "Unavailable";
    else if (s.state !== "on") st.textContent = "Off";
    else st.textContent = s.attributes.effect || (s.attributes.per_bulb ? "Painted" : "Colour");
    st.classList.toggle("bad", gone);
    this._strip?.classList.toggle("dis", gone);
    this._tools?.classList.toggle("dis", gone);
    const warn = root.querySelector(".hint.warn");
    const show = !gone && this._showActive();
    warn.hidden = !show;
    warn.textContent = show ? "A show is playing \u2014 your paint will be replaced." : "";
    root.querySelector(".paintinfo").textContent = gone
      ? ""
      : "Mouse: click or drag. Touch: press and hold on the bulbs, then drag \u2014 or turn on Paint.";
  }

  _error(text) {
    const el = this.shadowRoot?.querySelector(".err");
    if (!el) return;
    el.textContent = text || "";
    el.hidden = !text;
  }
}

customElements.define("govee-light-show-card", GoveeC9Card);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "govee-light-show-card",
  name: "Govee C9 string",
  description: "Every bulb of a Govee C9 string, live, with tap or drag to paint.",
  preview: true,
});
