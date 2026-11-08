// Light Show Studio: the 3D map viewer. A small canvas renderer (no libraries) used by the Map tab's area cards, the area
// view and the scan wizard. World space is a unit box: x right, y UP, z towards the viewer (docs/MAPPING_V2.md).
//
//   const v = new Viewer3D(canvas, { interactive: true, spin: 0, fit: false, onEdit, onEditEnd, onTap, onMode });
//   v.setDots({ n, pos: Float32Array(3n), base: Uint8ClampedArray(3n), groups: [{ start, len, rgb }], flags: Uint8Array(n) });
//   v.setLive(Uint8ClampedArray(3n) | null, Uint8Array(n) | null)    // live colours (lit = 1)
//   v.setHandles([{ id, pos: [x, y, z], rgb: [r, g, b], label, big }]);   // draggable end points (front/side view only)
//   v.setMode("3d" | "top" | "front" | "side");  v.reset();  v.destroy();
//   v.editable = "handles" | "points" | null    // what a drag moves in the front and side views

const TAU = Math.PI * 2;
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const reduced = () => {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch (e) {
    return false;
  }
};

const VIEWS = {
  "3d": { yaw: 0.62, pitch: 0.38, dist: 3.4 },
  front: { yaw: 0, pitch: 0, dist: 14 },
  side: { yaw: -Math.PI / 2, pitch: 0, dist: 14 },
  top: { yaw: 0, pitch: Math.PI / 2, dist: 14 }, // looking straight down: x right, z (towards the viewer) down the screen
};
// the two world axes each flat view shows (u across, v down) and the sign of their screen direction; w is the one it hides
export const VIEW_AXES = {
  top: { u: 0, v: 2, w: 1, su: 1, sv: 1 },
  front: { u: 0, v: 1, w: 2, su: 1, sv: -1 },
  side: { u: 2, v: 1, w: 0, su: -1, sv: -1 },
};

export class Viewer3D {
  constructor(canvas, opts = {}) {
    this.cv = canvas;
    this.opts = opts;
    this.interactive = !!opts.interactive;
    this.spin = opts.spin || 0; // degrees per second, previews only
    this.fit = !!opts.fit;
    this.editable = null;
    this.mode = opts.mode || "3d";
    const v = VIEWS[this.mode];
    this.yaw = v.yaw;
    this.pitch = v.pitch;
    this.dist = v.dist;
    this.zoom = 1;
    this.pan = [0, 0];
    this.centre = [0.5, 0.5, 0.5];
    this.fitZoom = 1;
    this.n = 0;
    this.pos = new Float32Array(0);
    this.base = new Uint8ClampedArray(0);
    this.live = null;
    this.lit = null;
    this.groups = [];
    this.flags = null;
    this.handles = [];
    this.sel = -1;
    this.lines = opts.lines !== false;
    this.dotScale = opts.dotScale || 1;
    this.ortho = !!opts.ortho; // flat views without perspective (the editor)
    this.maxZoom = opts.maxZoom || 8;
    this.tool = null; // {down(x, y, e) -> bool, move(x, y, e), up(x, y, e, moved), cancel()}: one-finger editing in the flat views
    this._dimOf = opts.dimOf || null;
    this._sx = new Float32Array(0);
    this._sy = new Float32Array(0);
    this._ss = new Float32Array(0);
    this._order = new Uint32Array(0);
    this._ptrs = new Map();
    this._tw = null;
    if (this.interactive) this._bind();
    if (typeof ResizeObserver === "function") {
      this._ro = new ResizeObserver(() => this.invalidate());
      this._ro.observe(canvas);
    }
    if (this.spin && !reduced()) this._startSpin();
  }

  destroy() {
    this._dead = true;
    this._ro?.disconnect();
    cancelAnimationFrame(this._raf);
    cancelAnimationFrame(this._twRaf);
    clearInterval(this._spinT);
    this._io?.disconnect();
  }

  // ---- data -----------------------------------------------------------------------------------------------------
  setDots({ n, pos, base, groups, flags }) {
    this.n = n;
    this.pos = pos;
    this.base = base;
    this.groups = groups || [];
    this.flags = flags || null;
    if (this._sx.length < n) {
      this._sx = new Float32Array(n);
      this._sy = new Float32Array(n);
      this._ss = new Float32Array(n);
      this._order = new Uint32Array(n);
    }
    if (this.live && this.live.length !== n * 3) this.live = this.lit = null;
    if (this.fit) this._fit();
    this.invalidate();
  }

  setLive(live, lit) {
    this.live = live;
    this.lit = lit;
    this.invalidate();
  }

  setHandles(list) {
    this.handles = list || [];
    this.invalidate();
  }

  setSelected(i) {
    this.sel = i;
    this.invalidate();
  }

  _fit() {
    if (!this.n) {
      this.centre = [0.5, 0.5, 0.5];
      this.fitZoom = 1;
      return;
    }
    const lo = [1e9, 1e9, 1e9], hi = [-1e9, -1e9, -1e9];
    for (let i = 0; i < this.n; i++) {
      for (let k = 0; k < 3; k++) {
        const v = this.pos[i * 3 + k];
        if (v < lo[k]) lo[k] = v;
        if (v > hi[k]) hi[k] = v;
      }
    }
    const ext = Math.max(hi[0] - lo[0], hi[1] - lo[1], (hi[2] - lo[2]) * 0.8, 0.12);
    this.centre = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
    this.fitZoom = clamp(0.95 / ext, 0.8, 5);
  }

  // ---- camera -----------------------------------------------------------------------------------------------------
  setMode(mode, animate = true) {
    if (!VIEWS[mode]) return;
    this.mode = mode;
    const v = VIEWS[mode];
    this._to({ yaw: v.yaw, pitch: v.pitch, dist: v.dist, pan: [0, 0], zoom: 1 }, animate);
    this.opts.onMode?.(mode);
  }

  reset(animate = true) {
    const v = VIEWS[this.mode];
    this._to({ yaw: v.yaw, pitch: v.pitch, dist: v.dist, pan: [0, 0], zoom: 1 }, animate);
  }

  _to(t, animate) {
    cancelAnimationFrame(this._twRaf);
    if (!animate || reduced() || !this.cv.isConnected) {
      this.yaw = t.yaw;
      this.pitch = t.pitch;
      this.dist = t.dist;
      this.pan = [...t.pan];
      this.zoom = t.zoom;
      this._tw = null;
      this.invalidate();
      return;
    }
    // go the short way round
    let dy = t.yaw - this.yaw;
    dy -= Math.round(dy / TAU) * TAU;
    const from = { yaw: this.yaw, pitch: this.pitch, dist: this.dist, pan: [...this.pan], zoom: this.zoom };
    const t0 = performance.now(), D = 380;
    const step = (now) => {
      if (this._dead) return;
      const k = clamp((now - t0) / D, 0, 1), e = 1 - Math.pow(1 - k, 3);
      this.yaw = from.yaw + dy * e;
      this.pitch = from.pitch + (t.pitch - from.pitch) * e;
      this.dist = from.dist + (t.dist - from.dist) * e;
      this.zoom = from.zoom + (t.zoom - from.zoom) * e;
      this.pan = [from.pan[0] + (t.pan[0] - from.pan[0]) * e, from.pan[1] + (t.pan[1] - from.pan[1]) * e];
      this._tw = k < 1;
      this.render();
      if (k < 1) this._twRaf = requestAnimationFrame(step);
      else this._tw = null;
    };
    this._tw = true;
    this._twRaf = requestAnimationFrame(step);
  }

  get settled() {
    return !this._tw;
  }

  _size() {
    const r = this.cv.getBoundingClientRect();
    return [Math.max(1, r.width), Math.max(1, r.height)];
  }

  // public helpers for editors: world -> [sx, sy], and screen -> world with the hidden axis fixed at w (flat views only)
  project(x, y, z) {
    const [W, H] = this._size();
    const o = this._proj(x, y, z, W, H, [0, 0, 0, 0]);
    return [o[0], o[1]];
  }

  unproject(sx, sy, w) {
    const ax = VIEW_AXES[this.mode];
    if (!ax) return null;
    const [W, H] = this._size(), c = this.centre;
    const dist = this.dist, f = Math.min(W, H) * 0.78 * this.zoom * (this.fit ? this.fitZoom : 1);
    const hidden = w - c[ax.w];
    // top looks down the y axis, the others along z or x; the depth of the hidden coordinate only matters for perspective
    const s = this.ortho ? f : (f * dist) / Math.max(0.2, dist - hidden);
    const out = [0, 0, 0];
    out[ax.u] = c[ax.u] + (ax.su * (sx - W / 2 - this.pan[0])) / s;
    out[ax.v] = c[ax.v] + (ax.sv * (sy - H / 2 - this.pan[1])) / s;
    out[ax.w] = w;
    return out;
  }

  pxPerUnit() {
    const [W, H] = this._size();
    return Math.min(W, H) * 0.78 * this.zoom * (this.fit ? this.fitZoom : 1);
  }

  panBy(dx, dy) {
    this.pan = [this.pan[0] + dx, this.pan[1] + dy];
    this.invalidate();
  }

  // zoom and pan the current flat view onto a box [lo, hi] (world units)
  focus(lo, hi, animate = true) {
    const ax = VIEW_AXES[this.mode];
    if (!ax) return;
    const [W, H] = this._size(), f0 = Math.min(W, H) * 0.78, c = this.centre;
    const eu = Math.max(0.1, hi[ax.u] - lo[ax.u]), ev = Math.max(0.1, hi[ax.v] - lo[ax.v]);
    const zoom = clamp(Math.min((W * 0.86) / (f0 * eu), (H * 0.86) / (f0 * ev)), 0.5, this.maxZoom);
    const s = f0 * zoom, mu = (lo[ax.u] + hi[ax.u]) / 2, mv = (lo[ax.v] + hi[ax.v]) / 2;
    const v = VIEWS[this.mode];
    this._to({ yaw: v.yaw, pitch: v.pitch, dist: v.dist, pan: [-ax.su * (mu - c[ax.u]) * s, -ax.sv * (mv - c[ax.v]) * s], zoom }, animate);
  }

  // world -> screen: [sx, sy, px per unit at that depth, depth]
  _proj(x, y, z, W, H, out) {
    const c = this.centre;
    x -= c[0];
    y -= c[1];
    z -= c[2];
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw), cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    const x1 = x * cy + z * sy, z1 = -x * sy + z * cy;
    const y2 = y * cp - z1 * sp, z2 = y * sp + z1 * cp;
    const d = this.dist;
    const f = Math.min(W, H) * 0.78 * this.zoom * (this.fit ? this.fitZoom : 1);
    const s = this.ortho && this.mode !== "3d" ? f : (f * d) / Math.max(0.2, d - z2);
    out[0] = W / 2 + this.pan[0] + s * x1;
    out[1] = H / 2 + this.pan[1] - s * y2;
    out[2] = s;
    out[3] = z2;
    return out;
  }

  // ---- drawing ----------------------------------------------------------------------------------------------------
  invalidate() {
    if (this._raf || this._dead) return;
    this._raf = requestAnimationFrame(() => {
      this._raf = 0;
      this.render();
    });
  }

  render() {
    if (this._dead) return;
    const cv = this.cv;
    const [W, H] = this._size();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
      cv.width = Math.round(W * dpr);
      cv.height = Math.round(H * dpr);
    }
    if (W < 4 || H < 4) return;
    const g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    // night backdrop
    const bg = g.createRadialGradient(W / 2, H * 0.45, 10, W / 2, H / 2, Math.max(W, H) * 0.75);
    bg.addColorStop(0, "#161b27");
    bg.addColorStop(1, "#07080c");
    g.fillStyle = bg;
    g.fillRect(0, 0, W, H);
    const p = [0, 0, 0, 0], q = [0, 0, 0, 0];
    const flat = this.mode !== "3d";
    this._grid(g, W, H, p, q, flat);
    // dots, back to front in 3D
    const n = this.n;
    const sx = this._sx, sy = this._sy, ss = this._ss;
    const dep = this._depth || (this._depth = new Float32Array(0));
    if (dep.length < n) this._depth = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      this._proj(this.pos[i * 3], this.pos[i * 3 + 1], this.pos[i * 3 + 2], W, H, p);
      sx[i] = p[0];
      sy[i] = p[1];
      ss[i] = p[2];
      this._depth[i] = p[3];
    }
    // faint string lines per group
    if (this.lines) {
      g.lineWidth = 1;
      for (const gr of this.groups) {
        if (gr.len < 2) continue;
        g.strokeStyle = `rgba(${gr.rgb[0]},${gr.rgb[1]},${gr.rgb[2]},${gr.sel ? 0.55 : gr.dim ? 0.08 : 0.2})`;
        g.beginPath();
        const step = Math.max(1, Math.floor(gr.len / 600));
        for (let i = 0; i < gr.len; i += step) {
          const k = gr.start + i;
          if (i) g.lineTo(sx[k], sy[k]);
          else g.moveTo(sx[k], sy[k]);
        }
        g.lineTo(sx[gr.start + gr.len - 1], sy[gr.start + gr.len - 1]);
        g.stroke();
      }
    }
    let order = null;
    if (!flat && n > 1 && n <= 5000) {
      order = this._order;
      for (let i = 0; i < n; i++) order[i] = i;
      const dp = this._depth;
      Array.prototype.sort.call(order.subarray(0, n), (a, b) => dp[a] - dp[b]);
    }
    const dotR = (this.opts.dotR || 0.0075) * this.dotScale;
    const base = this.base, live = this.live, lit = this.lit, flags = this.flags;
    const dimOf = this._dimOf || this.opts.dimOf || null;
    const glow = [];
    for (let k = 0; k < n; k++) {
      const i = order ? order[k] : k;
      const r = clamp(dotR * ss[i], 1.1, 10);
      if (lit && lit[i]) {
        glow.push(i);
        continue;
      }
      const o = i * 3;
      g.globalAlpha = dimOf ? dimOf(i) : 0.62;
      g.fillStyle = `rgb(${base[o]},${base[o + 1]},${base[o + 2]})`;
      g.beginPath();
      g.arc(sx[i], sy[i], r * 0.85, 0, TAU);
      g.fill();
    }
    g.globalAlpha = 1;
    if (glow.length) {
      g.globalCompositeOperation = "lighter";
      for (const i of glow) {
        const r = clamp(dotR * ss[i], 1.2, 10), o = i * 3;
        g.fillStyle = `rgba(${live[o]},${live[o + 1]},${live[o + 2]},0.2)`;
        g.beginPath();
        g.arc(sx[i], sy[i], r * 2.5, 0, TAU);
        g.fill();
      }
      g.globalCompositeOperation = "source-over";
      for (const i of glow) {
        const r = clamp(dotR * ss[i], 1.2, 10), o = i * 3;
        g.fillStyle = `rgb(${live[o]},${live[o + 1]},${live[o + 2]})`;
        g.beginPath();
        g.arc(sx[i], sy[i], r * 1.05, 0, TAU);
        g.fill();
      }
    }
    if (flags) {
      g.lineWidth = 1.5;
      for (let i = 0; i < n; i++) {
        if (flags[i] !== 1) continue; // estimated pixel: ringed in amber
        g.strokeStyle = "#ffb300";
        g.beginPath();
        g.arc(sx[i], sy[i], clamp(dotR * ss[i], 1.2, 10) + 2.2, 0, TAU);
        g.stroke();
      }
    }
    if (this.sel >= 0 && this.sel < n) {
      g.strokeStyle = "#fff";
      g.lineWidth = 2;
      g.beginPath();
      g.arc(sx[this.sel], sy[this.sel], clamp(dotR * ss[this.sel], 1.2, 10) + 5, 0, TAU);
      g.stroke();
    }
    // handles
    g.font = "600 12px system-ui, sans-serif";
    g.textAlign = "left";
    this._hpos = [];
    for (const h of this.handles) {
      this._proj(h.pos[0], h.pos[1], h.pos[2], W, H, q);
      const col = `rgb(${h.rgb[0]},${h.rgb[1]},${h.rgb[2]})`;
      g.globalAlpha = h.dim ? 0.4 : 1;
      g.beginPath();
      g.arc(q[0], q[1], h.big ? 11 : 9, 0, TAU);
      g.fillStyle = "#0b0d12";
      g.fill();
      g.lineWidth = 3;
      g.strokeStyle = col;
      g.stroke();
      if (h.big) {
        g.beginPath();
        g.arc(q[0], q[1], 3.5, 0, TAU);
        g.fillStyle = col;
        g.fill();
      }
      if (h.label) {
        const w = g.measureText(h.label).width;
        const lx = Math.max(6, Math.min(q[0] + 14, W - 8 - w)), ly = Math.max(14, q[1] - 14);
        g.fillStyle = "rgba(0,0,0,.6)";
        g.fillText(h.label, lx + 1, ly + 1);
        g.fillStyle = "#fff";
        g.fillText(h.label, lx, ly);
      }
      g.globalAlpha = 1;
      this._hpos.push({ h, x: q[0], y: q[1] });
    }
    if (this.opts.overlay) {
      try {
        this.opts.overlay(g, this, W, H);
      } catch (err) {
        /* an editor overlay must never stop the dots drawing */
      }
    }
    if (!flat) this._gizmo(g, W, H);
    this._draws = (this._draws || 0) + 1;
  }

  _grid(g, W, H, p, q, flat) {
    g.lineWidth = 1;
    const line = (a, b) => {
      this._proj(a[0], a[1], a[2], W, H, p);
      this._proj(b[0], b[1], b[2], W, H, q);
      g.moveTo(p[0], p[1]);
      g.lineTo(q[0], q[1]);
    };
    g.strokeStyle = "rgba(160,175,210,.10)";
    g.beginPath();
    if (!flat) {
      for (let i = 0; i <= 10; i++) {
        const t = i / 10;
        line([t, 0, 0], [t, 0, 1]);
        line([0, 0, t], [1, 0, t]);
      }
      g.stroke();
      // the box, so depth reads
      g.strokeStyle = "rgba(160,175,210,.16)";
      g.beginPath();
      for (const [a, b] of [[0, 0], [1, 0], [1, 1], [0, 1]]) {
        line([a, 0, b], [a, 1, b]);
      }
      line([0, 1, 0], [1, 1, 0]);
      line([1, 1, 0], [1, 1, 1]);
      line([1, 1, 1], [0, 1, 1]);
      line([0, 1, 1], [0, 1, 0]);
      g.stroke();
    } else {
      const front = this.mode === "front", top = this.mode === "top";
      for (let i = 0; i <= 10; i++) {
        const t = i / 10;
        if (top) {
          line([t, 0.5, 0], [t, 0.5, 1]);
          line([0, 0.5, t], [1, 0.5, t]);
        } else if (front) {
          line([t, 0, 0.5], [t, 1, 0.5]);
          line([0, t, 0.5], [1, t, 0.5]);
        } else {
          line([0.5, 0, t], [0.5, 1, t]);
          line([0.5, t, 0], [0.5, t, 1]);
        }
      }
      g.stroke();
      g.strokeStyle = "rgba(160,175,210,.3)";
      g.beginPath();
      if (top) {
        line([0, 0.5, 0], [1, 0.5, 0]);
        line([1, 0.5, 0], [1, 0.5, 1]);
        line([1, 0.5, 1], [0, 0.5, 1]);
        line([0, 0.5, 1], [0, 0.5, 0]);
      } else if (front) {
        line([0, 0, 0.5], [1, 0, 0.5]);
        line([1, 0, 0.5], [1, 1, 0.5]);
        line([1, 1, 0.5], [0, 1, 0.5]);
        line([0, 1, 0.5], [0, 0, 0.5]);
      } else {
        line([0.5, 0, 0], [0.5, 0, 1]);
        line([0.5, 0, 1], [0.5, 1, 1]);
        line([0.5, 1, 1], [0.5, 1, 0]);
        line([0.5, 1, 0], [0.5, 0, 0]);
      }
      g.stroke();
    }
  }

  _gizmo(g, W, H) {
    const ox = 34, oy = H - 30, L = 20;
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw), cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    g.font = "600 10px system-ui, sans-serif";
    g.textAlign = "center";
    g.lineWidth = 1.5;
    for (const [v, col, name] of [[[1, 0, 0], "#ff6b6b", "X"], [[0, 1, 0], "#6bdc8b", "Y"], [[0, 0, 1], "#6ba8ff", "Z"]]) {
      const x1 = v[0] * cy + v[2] * sy, z1 = -v[0] * sy + v[2] * cy, y2 = v[1] * cp - z1 * sp;
      g.strokeStyle = g.fillStyle = col;
      g.globalAlpha = 0.85;
      g.beginPath();
      g.moveTo(ox, oy);
      g.lineTo(ox + x1 * L, oy - y2 * L);
      g.stroke();
      g.fillText(name, ox + x1 * (L + 8), oy - y2 * (L + 8) + 3);
    }
    g.globalAlpha = 1;
  }

  // ---- spinning previews ---------------------------------------------------------------------------------------------
  _startSpin() {
    this._vis = true;
    if (typeof IntersectionObserver === "function") {
      this._io = new IntersectionObserver((e) => (this._vis = e[e.length - 1].isIntersecting));
      this._io.observe(this.cv);
    }
    let last = performance.now();
    this._spinT = setInterval(() => {
      const now = performance.now(), dt = (now - last) / 1000;
      last = now;
      if (!this._vis || document.hidden || this.mode !== "3d" || !this.cv.offsetParent || this._ptrs.size) return;
      this.yaw += (this.spin * Math.PI * dt) / 180;
      this.render();
    }, 66);
  }

  // ---- input ------------------------------------------------------------------------------------------------------------
  _bind() {
    const cv = this.cv;
    cv.style.touchAction = "none";
    cv.tabIndex = 0;
    cv.addEventListener("pointerdown", (e) => this._down(e));
    cv.addEventListener("pointermove", (e) => this._move(e));
    cv.addEventListener("pointerup", (e) => this._up(e));
    cv.addEventListener("pointercancel", (e) => this._up(e));
    cv.addEventListener("lostpointercapture", (e) => this._ptrs.delete(e.pointerId));
    cv.addEventListener("wheel", (e) => {
      e.preventDefault();
      this._zoomAt(Math.exp(-e.deltaY * 0.0015), e.offsetX, e.offsetY);
    }, { passive: false });
    cv.addEventListener("dblclick", (e) => {
      e.preventDefault();
      this.reset();
    });
    cv.addEventListener("keydown", (e) => {
      const k = e.key;
      if (k === "ArrowLeft" || k === "ArrowRight" || k === "ArrowUp" || k === "ArrowDown") {
        if (this.mode !== "3d") return;
        this.yaw += (k === "ArrowLeft" ? -1 : k === "ArrowRight" ? 1 : 0) * 0.12;
        this.pitch = clamp(this.pitch + (k === "ArrowUp" ? 1 : k === "ArrowDown" ? -1 : 0) * 0.1, -1.4, 1.4);
        this.invalidate();
        e.preventDefault();
      } else if (k === "+" || k === "=" || k === "-") {
        this.zoom = clamp(this.zoom * (k === "-" ? 0.85 : 1.18), 0.5, this.maxZoom);
        this.invalidate();
        e.preventDefault();
      } else if (k === "0" || k === "Escape") this.reset();
    });
  }

  _zoomAt(f, x, y) {
    const old = this.zoom;
    this.zoom = clamp(old * f, 0.5, this.maxZoom);
    const k = this.zoom / old, [W, H] = this._size();
    // keep the point under the cursor where it is
    this.pan = [(this.pan[0] + (W / 2 - x)) * k - (W / 2 - x), (this.pan[1] + (H / 2 - y)) * k - (H / 2 - y)];
    this.invalidate();
  }

  _xy(e) {
    const r = this.cv.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  _hit(x, y, touch) {
    if (!this._hpos || this.mode === "3d" || !this.settled) return null;
    let best = null, bd = touch ? 30 : 18;
    if (this.editable === "handles") {
      for (const hp of this._hpos) {
        const d = Math.hypot(hp.x - x, hp.y - y);
        if (d <= bd) {
          bd = d;
          best = { kind: "handle", h: hp.h };
        }
      }
    }
    if (!best && this.editable === "points") {
      bd = touch ? 26 : 14;
      for (let i = 0; i < this.n; i++) {
        const d = Math.hypot(this._sx[i] - x, this._sy[i] - y);
        if (d <= bd) {
          bd = d;
          best = { kind: "point", i };
        }
      }
    }
    return best;
  }

  _down(e) {
    const [x, y] = this._xy(e);
    this._ptrs.set(e.pointerId, { x, y, sx: x, sy: y, t: performance.now(), moved: false });
    try {
      this.cv.setPointerCapture(e.pointerId);
    } catch (err) {
      /* synthetic pointer */
    }
    if (this._ptrs.size === 1 && this.tool && this.mode !== "3d" && !(e.pointerType === "mouse" && (e.button === 2 || e.button === 1)) && this.tool.down(x, y, e)) {
      this._tdrag = true;
      this._drag = null;
      this._pan = false;
      e.preventDefault();
      return;
    }
    if (this._ptrs.size === 1) {
      const hit = e.pointerType === "mouse" && e.button === 2 ? null : this._hit(x, y, e.pointerType === "touch");
      this._drag = hit ? { hit, ref: hit.kind === "handle" ? hit.h.pos : this.pos.slice(hit.i * 3, hit.i * 3 + 3) } : null;
      if (hit?.kind === "point") {
        this.sel = hit.i;
        this.opts.onSelect?.(hit.i);
      }
      this._pan = !hit && (this.mode !== "3d" || e.shiftKey || e.button === 1 || e.button === 2);
      if (hit) e.preventDefault();
    } else {
      if (this._tdrag) {
        this._tdrag = false;
        this.tool?.cancel?.();
      }
      this._drag = null;
      this._pinch = this._pinchState();
    }
    // double tap
    const now = performance.now();
    if (this._ptrs.size === 1 && this._lastTap && now - this._lastTap.t < 320 && Math.hypot(x - this._lastTap.x, y - this._lastTap.y) < 30) {
      this.reset();
      this._lastTap = null;
    }
  }

  _pinchState() {
    const a = [...this._ptrs.values()];
    if (a.length < 2) return null;
    return { d: Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y) || 1, cx: (a[0].x + a[1].x) / 2, cy: (a[0].y + a[1].y) / 2 };
  }

  _move(e) {
    const p = this._ptrs.get(e.pointerId);
    if (!p) return;
    const [x, y] = this._xy(e);
    const dx = x - p.x, dy = y - p.y;
    p.x = x;
    p.y = y;
    if (Math.hypot(x - p.sx, y - p.sy) > 4) p.moved = true;
    if (this._ptrs.size >= 2) {
      const st = this._pinchState();
      if (this._pinch && st) {
        this._zoomAt(st.d / this._pinch.d, st.cx, st.cy);
        this.pan = [this.pan[0] + st.cx - this._pinch.cx, this.pan[1] + st.cy - this._pinch.cy];
      }
      this._pinch = st;
      this.invalidate();
      return;
    }
    if (this._tdrag) {
      this.tool.move(x, y, e, dx, dy, p.moved);
      return;
    }
    if (this._drag) {
      this._editTo(x, y);
      return;
    }
    if (!p.moved) return;
    if (this._pan) this.pan = [this.pan[0] + dx, this.pan[1] + dy];
    else if (this.mode === "3d") {
      this.yaw -= dx * 0.009;
      this.pitch = clamp(this.pitch + dy * 0.009, -1.45, 1.45);
    } else return;
    this.invalidate();
  }

  _up(e) {
    const p = this._ptrs.get(e.pointerId);
    this._ptrs.delete(e.pointerId);
    try {
      this.cv.releasePointerCapture(e.pointerId);
    } catch (err) {
      /* already released */
    }
    if (!p) return;
    if (this._tdrag) {
      this._tdrag = false;
      if (e.type === "pointercancel") this.tool?.cancel?.();
      else this.tool?.up(p.x, p.y, e, p.moved);
      if (this._ptrs.size < 2) this._pinch = null;
      return;
    }
    if (this._drag && this._drag.moved) this.opts.onEditEnd?.(this._drag.hit);
    else if (!p.moved && performance.now() - p.t < 400) {
      this._lastTap = { t: performance.now(), x: p.x, y: p.y };
      this.opts.onTap?.(p.x, p.y, this);
    }
    if (this._ptrs.size < 2) this._pinch = null;
    if (!this._ptrs.size) this._drag = null;
  }

  _editTo(sx, sy) {
    const d = this._drag;
    if (!d) return;
    const [W, H] = this._size();
    const f = Math.min(W, H) * 0.78 * this.zoom * (this.fit ? this.fitZoom : 1), dist = this.dist;
    const c = this.centre;
    let pos = [...d.ref];
    if (this.mode === "top") {
      const u = this.unproject(sx, sy, d.ref[1]);
      pos[0] = u[0];
      pos[2] = u[2];
    } else if (this.mode === "front") {
      const s = (f * dist) / Math.max(0.2, dist - (d.ref[2] - c[2]));
      pos[0] = c[0] + (sx - W / 2 - this.pan[0]) / s;
      pos[1] = c[1] - (sy - H / 2 - this.pan[1]) / s;
    } else {
      const s = (f * dist) / Math.max(0.2, dist - (d.ref[0] - c[0]));
      pos[2] = c[2] - (sx - W / 2 - this.pan[0]) / s;
      pos[1] = c[1] - (sy - H / 2 - this.pan[1]) / s;
    }
    pos = pos.map((v) => clamp(v, 0, 1));
    d.moved = true;
    if (d.hit.kind === "handle") d.hit.h.pos = pos;
    else {
      this.pos[d.hit.i * 3] = pos[0];
      this.pos[d.hit.i * 3 + 1] = pos[1];
      this.pos[d.hit.i * 3 + 2] = pos[2];
    }
    this.opts.onEdit?.(d.hit, pos);
    this.invalidate();
  }
}

// a stable, pleasant colour per run, as [r, g, b]
export function runRgb(i) {
  const h = ((i * 57 + 200) % 360) / 360, s = 0.85, l = 0.62;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => {
    const k = (n + h * 12) % 12;
    return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))));
  };
  return [f(0), f(8), f(4)];
}

// =====================================================================================================================
// Run editor: trace a run as a line, then fix or move its pixels. Used by the Map tab ("Trace and edit").
//
//   const ed = new RunEditor({ host, area: { id, name }, runs: [{ id, name, entry_id, first, count, pts, positions }],
//                              colourOf: (id) => [r, g, b], start: runId, onSave: async (results) => "" | "error text",
//                              onClose, probe: { can(run), light(run, i, n), stop() } | null, canEdit: true });
//
// results = [{ id, positions: [0-1000 ints, 3 per pixel], pts: [[x, y, z] 0-1, ...] }] for the runs that changed. Positions
// are stored exactly like a scan: x right, y UP, z towards the viewer, ints 0-1000 (docs/MAPPING_V2.md). `pts` keeps the
// traced corners so a trace can be adjusted later; for anything else it is the first and last pixel, like a scan.
// =====================================================================================================================

// ---- pure geometry (flat Float64Array of x, y, z per pixel, 0-1) -----------------------------------------------------
export const Geo = {
  // n points spread evenly by length along a polyline of corners [[x, y, z], ...]
  spread(corners, n) {
    const out = new Float64Array(Math.max(0, n) * 3), m = corners.length;
    if (!n || !m) return out;
    const cum = [0];
    for (let i = 1; i < m; i++) cum.push(cum[i - 1] + Math.hypot(corners[i][0] - corners[i - 1][0], corners[i][1] - corners[i - 1][1], corners[i][2] - corners[i - 1][2]));
    const total = cum[m - 1];
    let k = 1;
    for (let i = 0; i < n; i++) {
      if (m === 1 || total < 1e-9) {
        out.set(corners[0], i * 3);
        continue;
      }
      const d = n > 1 ? (i / (n - 1)) * total : total / 2;
      while (k < m - 1 && cum[k] < d) k++;
      const span = cum[k] - cum[k - 1] || 1e-9, t = clamp((d - cum[k - 1]) / span, 0, 1), a = corners[k - 1], b = corners[k];
      for (let c = 0; c < 3; c++) out[i * 3 + c] = a[c] + (b[c] - a[c]) * t;
    }
    return out;
  },

  length(corners) {
    let t = 0;
    for (let i = 1; i < corners.length; i++) t += Math.hypot(corners[i][0] - corners[i - 1][0], corners[i][1] - corners[i - 1][1], corners[i][2] - corners[i - 1][2]);
    return t;
  },

  // pixels a..b become a straight line from pixel a to pixel b, evenly spaced by index
  straighten(P, a, b) {
    for (let i = a + 1; i < b; i++) {
      const t = (i - a) / (b - a);
      for (let c = 0; c < 3; c++) P[i * 3 + c] = P[a * 3 + c] + (P[b * 3 + c] - P[a * 3 + c]) * t;
    }
  },

  // pull each pixel in a..b towards the middle of its neighbours (the run's two ends stay put)
  smooth(P, a, b, passes = 4, k = 0.5) {
    const n = P.length / 3, lo = Math.max(1, a), hi = Math.min(n - 2, b);
    for (let p = 0; p < passes; p++) {
      const src = P.slice();
      for (let i = lo; i <= hi; i++) for (let c = 0; c < 3; c++) P[i * 3 + c] = src[i * 3 + c] + k * ((src[(i - 1) * 3 + c] + src[(i + 1) * 3 + c]) / 2 - src[i * 3 + c]);
    }
  },

  // re-space pixels a..b evenly by length along the path they already follow
  even(P, a, b) {
    if (b - a < 2) return;
    const cum = [0];
    for (let i = a + 1; i <= b; i++) cum.push(cum[cum.length - 1] + Math.hypot(P[i * 3] - P[(i - 1) * 3], P[i * 3 + 1] - P[(i - 1) * 3 + 1], P[i * 3 + 2] - P[(i - 1) * 3 + 2]));
    const total = cum[cum.length - 1];
    if (total < 1e-9) return;
    const src = P.slice(a * 3, (b + 1) * 3);
    let k = 1;
    for (let j = 1; j < b - a; j++) {
      const d = (j / (b - a)) * total;
      while (k < cum.length - 1 && cum[k] < d) k++;
      const t = clamp((d - cum[k - 1]) / (cum[k] - cum[k - 1] || 1e-9), 0, 1);
      for (let c = 0; c < 3; c++) P[(a + j) * 3 + c] = src[(k - 1) * 3 + c] + (src[k * 3 + c] - src[(k - 1) * 3 + c]) * t;
    }
  },

  snap(P, a, b, axis, v) {
    for (let i = a; i <= b; i++) P[i * 3 + axis] = v;
  },

  mean(P, a, b, axis) {
    let s = 0;
    for (let i = a; i <= b; i++) s += P[i * 3 + axis];
    return s / Math.max(1, b - a + 1);
  },

  bounds(P, a, b) {
    const lo = [1e9, 1e9, 1e9], hi = [-1e9, -1e9, -1e9];
    for (let i = a; i <= b; i++) for (let c = 0; c < 3; c++) {
      const v = P[i * 3 + c];
      if (v < lo[c]) lo[c] = v;
      if (v > hi[c]) hi[c] = v;
    }
    return [lo, hi];
  },

  // rotate points a..b in the plane of axes u, v about a pivot; su, sv are the screen signs so a positive angle is clockwise on screen
  rotate(P, a, b, ax, pivot, th) {
    const cs = Math.cos(th), sn = Math.sin(th);
    for (let i = a; i <= b; i++) {
      const X = ax.su * (P[i * 3 + ax.u] - pivot[ax.u]), Y = ax.sv * (P[i * 3 + ax.v] - pivot[ax.v]);
      P[i * 3 + ax.u] = pivot[ax.u] + ax.su * (X * cs - Y * sn);
      P[i * 3 + ax.v] = pivot[ax.v] + ax.sv * (X * sn + Y * cs);
    }
  },

  // soft weight of the pixel j steps away from a dragged one (1 at the dot, 0 at radius)
  falloff(j, radius) {
    if (!radius) return j === 0 ? 1 : 0;
    const t = Math.abs(j) / (radius + 1);
    return t >= 1 ? 0 : 0.5 * (1 + Math.cos(Math.PI * t));
  },

  reverse(P) {
    const n = P.length / 3, out = new Float64Array(P.length);
    for (let i = 0; i < n; i++) for (let c = 0; c < 3; c++) out[i * 3 + c] = P[(n - 1 - i) * 3 + c];
    P.set(out);
  },

  clampAll(P, a, b) {
    for (let i = a * 3; i < (b + 1) * 3; i++) P[i] = clamp(P[i], 0, 1);
  },
};

const HEIGHTS = [["Floor / toe-kick", 0.06], ["Counter", 0.38], ["Above cabinets", 0.88], ["Ceiling", 0.98]];
const pct = (v) => Math.round(v * 100) + "%";
const ICO = {
  close: "M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z",
  undo: "M12.5 8c-2.65 0-5.05.99-6.9 2.6L2 7v9h9l-3.62-3.62c1.39-1.16 3.16-1.88 5.12-1.88 3.54 0 6.55 2.31 7.6 5.5l2.37-.78C21.08 11.03 17.15 8 12.5 8z",
  redo: "M18.4 10.6C16.55 8.99 14.15 8 11.5 8c-4.65 0-8.58 3.03-9.96 7.22L3.9 16c1.05-3.19 4.05-5.5 7.6-5.5 1.95 0 3.73.72 5.12 1.88L13 16h9V7l-3.6 3.6z",
  trace: "M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z",
  dot: "M10 9h4V6h3l-5-5-5 5h3v3zm-1 1H6V7l-5 5 5 5v-3h3v-4zm14 2-5-5v3h-3v4h3v3l5-5zm-9 3h-4v3H7l5 5 5-5h-3v-3z",
  select: "M3 5h2V3c-1.1 0-2 .9-2 2zm0 8h2v-2H3v2zm4 8h2v-2H7v2zM3 9h2V7H3v2zm10-6h-2v2h2V3zm6 0v2h2c0-1.1-.9-2-2-2zM5 21v-2H3c0 1.1.9 2 2 2zm-2-4h2v-2H3v2zM9 3H7v2h2V3zm2 18h2v-2h-2v2zm8-8h2v-2h-2v2zm0 8c1.1 0 2-.9 2-2h-2v2zm0-12h2V7h-2v2zm0 8h2v-2h-2v2zm-4 4h2v-2h-2v2zm0-16h2V3h-2v2z",
  run: "M21 11 17 7v3H7V7l-4 4 4 4v-3h10v3z",
  fit: "M3 5v4h2V5h4V3H5c-1.1 0-2 .9-2 2zm2 10H3v4c0 1.1.9 2 2 2h4v-2H5v-4zm14 4h-4v2h4c1.1 0 2-.9 2-2v-4h-2v4zm0-16h-4v2h4v4h2V5c0-1.1-.9-2-2-2z",
  bulb: "M9 21c0 .55.45 1 1 1h4c.55 0 1-.45 1-1v-1H9v1zm3-19C8.14 2 5 5.14 5 9c0 2.38 1.19 4.47 3 5.74V17c0 .55.45 1 1 1h6c.55 0 1-.45 1-1v-2.26c1.81-1.27 3-3.36 3-5.74 0-3.86-3.14-7-7-7z",
};
const sv = (d, s = 20) => `<svg viewBox="0 0 24 24" width="${s}" height="${s}" aria-hidden="true"><path fill="currentColor" d="${d}"/></svg>`;

const RE_CSS = `
.rte { position: fixed; inset: 0; z-index: 31; display: flex; flex-direction: column; background: var(--lss-bg); color: var(--lss-text); font-size: 14px; }
.rte button { font: inherit; }
.rte-h { display: flex; align-items: center; gap: 4px; min-height: 56px; padding: 0 6px 0 14px; flex: none; background: var(--app-header-background-color, var(--lss-acc));
         color: var(--app-header-text-color, var(--lss-onacc)); box-shadow: 0 2px 8px rgba(0,0,0,.18); }
.rte-h h2 { margin: 0; font-size: 17px; font-weight: 600; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rte-h .ib2 { color: inherit; }
.rte-h .ib2:hover:not(:disabled) { background: rgba(255,255,255,.18); }
.rte-h .ib2:disabled { opacity: .35; }
.rte-save { min-height: 44px; padding: 0 18px; border-radius: 22px; border: 0; font-weight: 700; cursor: pointer; background: #fff; color: #111; margin-left: 4px; display: inline-flex; align-items: center; gap: 8px; }
.rte-save:disabled { background: rgba(255,255,255,.22); color: inherit; cursor: default; }
.rte-save i { width: 9px; height: 9px; border-radius: 50%; background: #e8590c; }
.rte-chips { display: flex; gap: 8px; padding: 8px 12px; overflow-x: auto; flex: none; border-bottom: 1px solid var(--lss-line); background: var(--lss-card); scrollbar-width: thin; }
.rte-chip { flex: none; display: inline-flex; align-items: center; gap: 8px; min-height: 44px; padding: 4px 14px; border-radius: 999px; border: 1px solid var(--lss-line); background: var(--lss-card); color: var(--lss-text); cursor: pointer; }
.rte-chip[aria-pressed=true] { border-color: var(--c); box-shadow: 0 0 0 2px var(--c) inset; font-weight: 600; }
.rte-chip .rsw { width: 12px; height: 12px; border-radius: 50%; background: var(--c); box-shadow: 0 0 6px var(--c); flex: none; }
.rte-chip small { color: var(--lss-mut); font-size: 11.5px; }
.rte-main { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.rte-view { position: relative; flex: 1 1 auto; min-height: 220px; background: #0b0d12; overflow: hidden; }
.rte-view canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; touch-action: none; }
.rte-vt { position: absolute; top: 8px; left: 8px; right: 8px; display: flex; gap: 8px; justify-content: space-between; align-items: flex-start; pointer-events: none; z-index: 2; }
.rte-vt > * { pointer-events: auto; }
.rte-seg { display: flex; padding: 3px; gap: 3px; border-radius: 14px; background: rgba(0,0,0,.6); border: 1px solid rgba(255,255,255,.18); }
.rte-seg button { min-height: 40px; min-width: 52px; padding: 0 10px; border-radius: 11px; border: 0; background: transparent; color: #fff; cursor: pointer; font-weight: 500; }
.rte-seg button[aria-pressed=true] { background: var(--lss-acc); color: var(--lss-onacc); font-weight: 700; }
.rte-tb { display: flex; gap: 6px; }
.rte-tb button { width: 44px; height: 44px; border-radius: 50%; border: 1px solid rgba(255,255,255,.18); background: rgba(0,0,0,.6); color: #fff; display: inline-flex; align-items: center; justify-content: center; cursor: pointer; }
.rte-msg { position: absolute; left: 8px; right: 8px; bottom: 8px; z-index: 2; padding: 8px 12px; border-radius: 12px; background: rgba(0,0,0,.72); color: #fff; font-size: 13px; line-height: 1.35; pointer-events: none; }
.rte-msg.err { background: rgba(190,40,30,.92); }
.rte-side { flex: none; max-height: 46vh; overflow-y: auto; border-top: 1px solid var(--lss-line); background: var(--lss-card); padding-bottom: env(safe-area-inset-bottom, 0px); }
.rte-tools { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; padding: 8px 10px 4px; position: sticky; top: 0; background: var(--lss-card); z-index: 1; }
.rte-tools button { min-height: 56px; border-radius: 12px; border: 1px solid var(--lss-line); background: var(--lss-soft); color: var(--lss-text); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; font-size: 12.5px; cursor: pointer; padding: 4px 2px; }
.rte-tools button[aria-pressed=true] { background: var(--lss-acc); border-color: var(--lss-acc); color: var(--lss-onacc); font-weight: 700; }
.rte-p { padding: 8px 12px 14px; display: grid; gap: 12px; }
.rte-p .hint { color: var(--lss-mut); font-size: 13px; line-height: 1.4; }
.rte-p h4 { margin: 0 0 6px; font-size: 13px; font-weight: 600; color: var(--lss-mut); text-transform: uppercase; letter-spacing: .04em; }
.rte-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.rte-b { min-height: 44px; padding: 6px 14px; border-radius: 12px; border: 1px solid var(--lss-line); background: var(--lss-soft); color: var(--lss-text); cursor: pointer; font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
.rte-b:hover:not(:disabled) { background: var(--lss-soft2); }
.rte-b:disabled { opacity: .4; cursor: default; }
.rte-b.pri { background: var(--lss-acc); border-color: var(--lss-acc); color: var(--lss-onacc); }
.rte-b[aria-pressed=true] { background: var(--lss-acc); border-color: var(--lss-acc); color: var(--lss-onacc); }
.rte-b.warn { color: var(--lss-err); }
.rte-b small { font-weight: 400; opacity: .8; }
.rte-sl { display: grid; grid-template-columns: 1fr auto; gap: 0 10px; align-items: center; }
.rte-sl label { font-size: 13px; color: var(--lss-mut); grid-column: 1 / 2; }
.rte-sl output { font-variant-numeric: tabular-nums; font-weight: 700; min-width: 3.4em; text-align: right; }
.rte-sl input[type=range] { grid-column: 1 / 3; width: 100%; min-height: 44px; accent-color: var(--lss-acc); margin: 0; }
.rte-pad { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
.rte-pad .rte-b { justify-content: center; padding: 6px 4px; }
.rte-err { color: var(--lss-err); font-weight: 600; }
.rte-split { display: grid; gap: 12px; }
@media (min-width: 900px) {
  .rte-main { flex-direction: row; }
  .rte-side { width: 400px; max-height: none; border-top: 0; border-left: 1px solid var(--lss-line); }
  .rte-view { min-height: 0; }
}
@media (max-height: 560px) and (orientation: landscape) { .rte-main { flex-direction: row; } .rte-side { width: 46vw; max-height: none; border-top: 0; border-left: 1px solid var(--lss-line); } .rte-view { min-height: 0; } }
@media (prefers-reduced-motion: reduce) { .rte * { transition: none !important; } }
`;

export class RunEditor {
  constructor(o) {
    this.o = o;
    this.canEdit = o.canEdit !== false;
    this.runs = [];
    for (const raw of o.runs || []) {
      const r = this._load(raw, o.colourOf ? o.colourOf(raw.id) : [150, 170, 255]);
      if (r) this.runs.push(r);
    }
    this.cur = this.runs.find((r) => r.id === o.start) || this.runs[0] || null;
    this.tool = "trace";
    this.view = "top";
    this.sel = null; // {a, b} pixel range of the current run, or null (= the whole run for the edit buttons)
    this.focusPx = 0; // the pixel the probe lights
    this.cornerSel = -1;
    this.H = 0.38; // height of new corners
    this.softR = 0; // soft move radius, pixels
    this.gridSnap = true;
    this.moveTarget = "run";
    this.nudgeStep = 0.02;
    this.snapAxis = 1;
    this.snapV = 0.5;
    this.probeOn = false;
    this.probeMode = "pixel";
    this.msg = "";
    this.msgErr = false;
    this.hist = [];
    this.hi = -1;
    this._hid = 0;
    this._commit(true);
    this.savedId = this.hist[this.hi].id;
    this._mount();
    this._build();
    this._renderAll();
    requestAnimationFrame(() => this.zoomToRun(false));
  }

  // ---- model --------------------------------------------------------------------------------------------------------
  _load(raw, rgb) {
    const n = Math.max(0, Math.min(2040, Math.floor(raw.count) || 0));
    if (!n) return null;
    const p3 = (p) => [Number(p[0]) || 0, Number(p[1]) || 0, p.length > 2 ? Number(p[2]) || 0 : 0.5];
    const pts = Array.isArray(raw.pts) ? raw.pts.map(p3) : [];
    const r = { id: raw.id, name: raw.name || "Run", entry_id: raw.entry_id, first: raw.first || 0, count: n, rgb, raw, P: new Float64Array(n * 3), corners: null, kind: "line", fresh: true };
    if (Array.isArray(raw.positions) && raw.positions.length === n * 3) {
      for (let i = 0; i < n * 3; i++) r.P[i] = raw.positions[i] / 1000;
      r.kind = "scan";
      r.fresh = false;
      if (pts.length >= 2) {
        const S = Geo.spread(pts, n);
        let d = 0;
        for (let i = 0; i < S.length; i++) d = Math.max(d, Math.abs(S[i] - r.P[i]));
        if (d <= 0.002) {
          r.corners = pts;
          r.kind = "traced";
        }
      }
    } else if (pts.length === n && n > 2) {
      r.P.set(pts.flat());
      r.kind = "imported";
      r.fresh = false;
    } else {
      r.corners = pts.length ? pts : [];
      r.P = pts.length ? Geo.spread(pts, n) : new Float64Array(n * 3).fill(0.5);
      r.fresh = pts.length <= 2;
      if (pts.length > 2) r.kind = "traced";
    }
    r.orig = this._key(r);
    return r;
  }

  _ints(r) {
    const out = new Array(r.P.length);
    for (let i = 0; i < out.length; i++) out[i] = clamp(Math.round(r.P[i] * 1000), 0, 1000);
    return out;
  }

  _outPts(r) {
    const R = (v) => Math.round(clamp(v, 0, 1) * 10000) / 10000;
    if (r.corners && r.corners.length >= 2) return r.corners.map((c) => c.map(R));
    const n = r.count;
    return [0, n - 1].map((i) => [R(r.P[i * 3]), R(r.P[i * 3 + 1]), R(r.P[i * 3 + 2])]);
  }

  _key(r) {
    return JSON.stringify([this._ints(r), this._outPts(r)]);
  }

  _changed(r) {
    return this._key(r) !== r.orig;
  }

  _snapshot() {
    return this.runs.map((r) => ({ id: r.id, P: new Float64Array(r.P), corners: r.corners ? r.corners.map((c) => [...c]) : null, kind: r.kind, fresh: r.fresh }));
  }

  _restore(snap) {
    for (const s of snap) {
      const r = this.runs.find((x) => x.id === s.id);
      if (!r) continue;
      r.P.set(s.P);
      r.corners = s.corners ? s.corners.map((c) => [...c]) : null;
      r.kind = s.kind;
      r.fresh = s.fresh;
    }
  }

  _commit(first) {
    this.hist.length = this.hi + 1;
    this.hist.push({ id: ++this._hid, snap: this._snapshot() });
    if (this.hist.length > 80) this.hist.shift();
    this.hi = this.hist.length - 1;
    if (!first) this._after();
  }

  get dirty() {
    return this.hist[this.hi].id !== this.savedId;
  }

  undo() {
    if (this.hi <= 0) return;
    this.hi--;
    this._restore(this.hist[this.hi].snap);
    this.cornerSel = -1;
    this._after();
  }

  redo() {
    if (this.hi >= this.hist.length - 1) return;
    this.hi++;
    this._restore(this.hist[this.hi].snap);
    this.cornerSel = -1;
    this._after();
  }

  _after() {
    this._syncDots();
    this._renderHead();
    this._renderChips();
    this._renderPanel();
  }

  // ---- DOM ----------------------------------------------------------------------------------------------------------
  _el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  _b(text, cls, fn, icon) {
    const b = this._el("button", cls);
    b.type = "button";
    if (icon) b.innerHTML = sv(icon, 18);
    if (text) b.append(document.createTextNode(text));
    b.onclick = fn;
    return b;
  }

  _mount() {
    const r = (this.root = this._el("div", "rte"));
    r.setAttribute("role", "dialog");
    r.setAttribute("aria-modal", "true");
    r.setAttribute("aria-label", "Trace and edit runs");
    const st = this._el("style");
    st.textContent = RE_CSS;
    this.head = this._el("div", "rte-h");
    this.chips = this._el("div", "rte-chips");
    this.chips.setAttribute("role", "group");
    this.chips.setAttribute("aria-label", "Runs");
    this.main = this._el("div", "rte-main");
    this.vw = this._el("div", "rte-view");
    this.side = this._el("div", "rte-side");
    this.main.append(this.vw, this.side);
    r.append(st, this.head, this.chips, this.main);
    this.o.host.append(r);
    r.addEventListener("keydown", (e) => {
      const k = e.key.toLowerCase(), mod = e.metaKey || e.ctrlKey;
      if (e.target.matches?.("input[type=text],input[type=number],textarea")) return;
      if (mod && k === "z") {
        e.preventDefault();
        if (e.shiftKey) this.redo();
        else this.undo();
      } else if (mod && k === "y") {
        e.preventDefault();
        this.redo();
      } else if (mod && k === "s") {
        e.preventDefault();
        this.save();
      } else if (k === "escape") this.close();
      else if ((k === "delete" || k === "backspace") && this.tool === "trace" && this.cornerSel >= 0) {
        e.preventDefault();
        this._deleteCorner();
      }
    });
    r.tabIndex = -1;
    r.focus({ preventScroll: true });
  }

  _build() {
    const cv = this._el("canvas");
    cv.setAttribute("role", "img");
    cv.setAttribute("aria-label", "Plan of the room. One finger edits with the chosen tool, two fingers pinch to zoom and pan.");
    this.vw.append(cv);
    // dots of every run in one buffer
    let n = 0;
    for (const r of this.runs) {
      r.off = n;
      n += r.count;
    }
    this.n = n;
    this.pos = new Float32Array(n * 3);
    this.base = new Uint8ClampedArray(n * 3);
    this.runOf = new Uint16Array(n);
    this.runs.forEach((r, k) => {
      for (let i = 0; i < r.count; i++) {
        this.base.set(r.rgb, (r.off + i) * 3);
        this.runOf[r.off + i] = k;
      }
    });
    this.viewer = new Viewer3D(cv, {
      interactive: true, fit: false, ortho: true, maxZoom: 40, dotR: 0.0075, lines: false, mode: "top",
      dimOf: (i) => {
        const r = this.runs[this.runOf[i]];
        if (r !== this.cur) return 0.38;
        if (this.sel && i - r.off >= this.sel.a && i - r.off <= this.sel.b) return 1;
        return this.sel ? 0.55 : 0.95;
      },
      overlay: (g, v, W, H) => this._overlay(g, v, W, H),
      onTap: (x, y) => this._view3dTap(x, y),
      onMode: () => {},
    });
    this.viewer.tool = this._toolImpl();
    this.viewer.setDots({ n, pos: this.pos, base: this.base, groups: [] });
    this._syncDots();
    // top-left: views; top-right: zoom
    const vt = this._el("div", "rte-vt");
    this.seg = this._el("div", "rte-seg");
    this.seg.setAttribute("role", "group");
    this.seg.setAttribute("aria-label", "View");
    const tb = this._el("div", "rte-tb");
    tb.append(
      Object.assign(this._b("", "", () => this.zoomToRun(true), ICO.fit), { title: "Zoom to this run" }),
      Object.assign(this._b("", "", () => this.viewer.reset(true), "M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 12 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"), { title: "Show the whole room" }),
    );
    tb.children[0].setAttribute("aria-label", "Zoom to this run");
    tb.children[1].setAttribute("aria-label", "Show the whole room");
    vt.append(this.seg, tb);
    this.msgEl = this._el("div", "rte-msg");
    this.msgEl.setAttribute("aria-live", "polite");
    this.msgEl.hidden = true;
    this.vw.append(vt, this.msgEl);
    this.viewer.setMode("top", false);
  }

  destroy() {
    clearTimeout(this._probeT);
    this.viewer?.destroy();
    this.root?.remove();
  }

  async close(force) {
    if (this.dirty && !force && !window.confirm("Close without saving? Your edits to the runs will be lost.")) return;
    clearTimeout(this._probeT);
    this.o.probe?.stop?.();
    this.destroy();
    this.o.onClose?.();
  }

  say(text, err) {
    this.msg = text || "";
    this.msgErr = !!err;
    this.msgEl.hidden = !text;
    this.msgEl.textContent = text || "";
    this.msgEl.classList.toggle("err", !!err);
    clearTimeout(this._msgT);
    if (text && !err) this._msgT = setTimeout(() => this.say(""), 5000);
  }

  _renderAll() {
    this._renderHead();
    this._renderChips();
    this._renderSeg();
    this._renderPanel();
  }

  _renderHead() {
    const h = this.head;
    h.textContent = "";
    const x = this._b("", "ib2", () => this.close(), ICO.close);
    x.setAttribute("aria-label", "Close the editor");
    const t = this._el("h2", "", `Trace · ${this.o.area?.name || ""}`);
    const un = this._b("", "ib2", () => this.undo(), ICO.undo);
    un.setAttribute("aria-label", "Undo");
    un.title = "Undo (Ctrl/Cmd+Z)";
    un.disabled = this.hi <= 0;
    const re = this._b("", "ib2", () => this.redo(), ICO.redo);
    re.setAttribute("aria-label", "Redo");
    re.title = "Redo (Ctrl/Cmd+Shift+Z)";
    re.disabled = this.hi >= this.hist.length - 1;
    const sb = this._el("button", "rte-save");
    sb.type = "button";
    const dirty = this.dirty;
    if (this._saving) sb.textContent = "Saving...";
    else if (dirty) sb.innerHTML = "<i></i>Save";
    else sb.textContent = "Saved";
    sb.disabled = !dirty || !this.canEdit || !!this._saving;
    sb.setAttribute("aria-label", dirty ? "Save the edited runs. You have unsaved changes." : "No unsaved changes");
    sb.onclick = () => this.save();
    h.append(x, t, un, re, sb);
    this.dirtyEl = sb;
  }

  _renderChips() {
    const c = this.chips;
    c.textContent = "";
    for (const r of this.runs) {
      const b = this._el("button", "rte-chip");
      b.type = "button";
      b.style.setProperty("--c", `rgb(${r.rgb.join(",")})`);
      b.setAttribute("aria-pressed", String(r === this.cur));
      const tag = this._changed(r) ? "unsaved" : r.kind === "scan" ? "scan" : r.kind === "traced" ? "traced" : r.kind === "imported" ? "imported" : r.kind === "edited" ? "edited" : r.fresh ? "not traced" : "line";
      b.innerHTML = `<span class="rsw"></span>`;
      b.append(this._el("span", "", r.name), this._el("small", "", `${r.count} px · ${tag}`));
      b.onclick = () => this.selectRun(r);
      c.append(b);
    }
    if (!this.runs.length) c.append(this._el("span", "hint", "This area has no runs with a pixel count yet."));
  }

  _renderSeg() {
    const s = this.seg;
    s.textContent = "";
    for (const [id, t] of [["top", "Top"], ["front", "Front"], ["side", "Side"], ["3d", "3D"]]) {
      const b = this._el("button", "", t);
      b.type = "button";
      b.setAttribute("aria-pressed", String(this.view === id));
      b.onclick = () => this.setView(id, true);
      s.append(b);
    }
  }

  setView(v, animate) {
    this.view = v;
    this.viewer.setMode(v, v === "3d" && animate !== false);
    this._renderSeg();
    this.say(v === "3d" ? "3D is for looking. Switch to Top, Front or Side to edit." : "");
    this._renderPanel();
    if (v !== "3d") this.zoomToRun(false);
  }

  zoomToRun(animate) {
    const r = this.cur;
    if (!r || this.view === "3d") return;
    const [lo, hi] = Geo.bounds(r.P, 0, r.count - 1);
    // pad small runs so they are not blown up to the whole screen
    const ax = VIEW_AXES[this.view];
    for (const k of [ax.u, ax.v]) {
      const mid = (lo[k] + hi[k]) / 2, e = Math.max(hi[k] - lo[k], 0.25);
      lo[k] = mid - e / 2;
      hi[k] = mid + e / 2;
    }
    this.viewer.focus(lo, hi, animate);
  }

  selectRun(r, keepView) {
    if (r === this.cur) return;
    this.cur = r;
    this.sel = null;
    this.cornerSel = -1;
    this.focusPx = 0;
    this._renderChips();
    this._renderPanel();
    this.viewer.invalidate();
    if (!keepView) this.zoomToRun(true);
  }

  _view3dTap(x, y) {
    // in 3D a tap on a dot picks that run
    const hit = this._nearestDot(x, y, 22, null);
    if (hit && this.runs[this.runOf[hit.k]] !== this.cur) this.selectRun(this.runs[this.runOf[hit.k]], true);
  }

  _syncDots() {
    for (const r of this.runs) for (let i = 0; i < r.P.length; i++) this.pos[r.off * 3 + i] = r.P[i];
    this.viewer?.invalidate();
  }

  // ---- hit testing --------------------------------------------------------------------------------------------------
  // the nearest dot to a screen point: of one run (r), or of any run when r is null. Returns {k (global), i (in run), run, d}
  _nearestDot(x, y, maxD, r) {
    let best = null, bd = maxD;
    const runs = r ? [r] : this.runs;
    for (const rr of runs) {
      for (let i = 0; i < rr.count; i++) {
        const p = this.viewer.project(rr.P[i * 3], rr.P[i * 3 + 1], rr.P[i * 3 + 2]);
        const d = Math.hypot(p[0] - x, p[1] - y);
        if (d < bd) {
          bd = d;
          best = { k: rr.off + i, i, run: rr, d };
        }
      }
    }
    return best;
  }

  _nearLine(x, y, r) {
    // distance from a screen point to the run's path (every few pixels)
    let bd = 1e9;
    const step = Math.max(1, Math.floor(r.count / 400));
    let pp = null;
    for (let i = 0; i < r.count; i += step) {
      const p = this.viewer.project(r.P[i * 3], r.P[i * 3 + 1], r.P[i * 3 + 2]);
      if (pp) bd = Math.min(bd, segDist(x, y, pp[0], pp[1], p[0], p[1]));
      else bd = Math.min(bd, Math.hypot(p[0] - x, p[1] - y));
      pp = p;
    }
    return bd;
  }
}

function segDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
  const t = l2 ? clamp(((px - ax) * dx + (py - ay) * dy) / l2, 0, 1) : 0;
  return Math.hypot(px - (ax + dx * t), py - (ay + dy * t));
}

// ---- tools: one finger edits in the flat views, two fingers pan and zoom -------------------------------------------------
const isTouch = (e) => e.pointerType === "touch" || e.pointerType === "pen";
const GRID = 0.025;

Object.assign(RunEditor.prototype, {
  _toolImpl() {
    const ed = this;
    return {
      down(x, y, e) {
        ed._st = null;
        if (!ed.cur) return false;
        if (ed.tool === "trace") return ed._dTrace(x, y, e);
        if (ed.tool === "dot") return ed._dDot(x, y, e);
        if (ed.tool === "select") return ed._dSelect(x, y, e);
        if (ed.tool === "run") return ed._dRun(x, y, e);
        return false;
      },
      move(x, y, e, dx, dy) {
        const s = ed._st;
        if (!s) return;
        if (s.k === "pan" || (s.k === "add" || s.k === "switch" || s.k === "clear") ) {
          if (!s.panned && Math.hypot(x - s.x, y - s.y) > 8) {
            s.panned = true;
            ed.viewer.panBy(x - s.x, y - s.y);
          } else if (s.panned) ed.viewer.panBy(dx, dy);
          return;
        }
        ed._mv(x, y, e, s);
      },
      up(x, y, e, moved) {
        const s = ed._st;
        ed._st = null;
        if (!s) return;
        ed._upSt(x, y, e, s);
      },
      cancel() {
        const s = ed._st;
        ed._st = null;
        if (s && s.dirty) {
          ed._restore(ed.hist[ed.hi].snap);
          ed._syncDots();
          ed._renderPanel();
        }
      },
    };
  },

  _ax() {
    return VIEW_AXES[this.view];
  },

  _defaultW() {
    const ax = this._ax(), cs = this.cur.corners;
    if (ax.w === 1) return this.H;
    const last = cs && cs.length ? cs[cs.length - 1][ax.w] : 0.5;
    return last;
  },

  // snap a world position to another run's corner or end (the "magnet"), else to the grid
  _snapPos(pos, sx, sy, skipRun, skipCorner) {
    const ax = this._ax();
    let best = null, bd = 14;
    for (const rr of this.runs) {
      const cands = rr.corners && rr.corners.length ? rr.corners : [[...rr.P.slice(0, 3)], [...rr.P.slice((rr.count - 1) * 3, rr.count * 3)]];
      cands.forEach((c, i) => {
        if (rr === skipRun && i === skipCorner) return;
        const p = this.viewer.project(c[0], c[1], c[2]);
        const d = Math.hypot(p[0] - sx, p[1] - sy);
        if (d < bd) {
          bd = d;
          best = c;
        }
      });
    }
    if (best) {
      pos[ax.u] = best[ax.u];
      pos[ax.v] = best[ax.v];
    } else if (this.gridSnap) {
      pos[ax.u] = Math.round(pos[ax.u] / GRID) * GRID;
      pos[ax.v] = Math.round(pos[ax.v] / GRID) * GRID;
    }
    return pos.map((v) => clamp(v, 0, 1));
  },

  // -- trace
  _dTrace(x, y, e) {
    const r = this.cur, tch = isTouch(e);
    if (r.corners && r.corners.length) {
      let bi = -1, bd = tch ? 30 : 18;
      r.corners.forEach((c, i) => {
        const p = this.viewer.project(c[0], c[1], c[2]), d = Math.hypot(p[0] - x, p[1] - y);
        if (d < bd) {
          bd = d;
          bi = i;
        }
      });
      if (bi >= 0) {
        this.cornerSel = bi;
        this._st = { k: "corner", i: bi, moved: false, x, y };
        this.viewer.invalidate();
        return true;
      }
    }
    this._st = { k: "add", x, y, panned: false };
    return true;
  },

  _respread(r) {
    r.P.set(Geo.spread(r.corners, r.count));
    r.kind = "traced";
    r.fresh = false;
    this._syncDots();
  },

  _addCorner(x, y) {
    const r = this.cur, ax = this._ax();
    let pos = this.viewer.unproject(x, y, this._defaultW());
    pos = this._snapPos(pos, x, y, r, -1);
    if (!r.corners || r.fresh) {
      r.corners = [];
      r.fresh = false;
    }
    // near the line between two corners: insert there, else extend the end
    let at = r.corners.length;
    if (r.corners.length >= 2) {
      let bd = 12;
      for (let i = 1; i < r.corners.length; i++) {
        const a = this.viewer.project(...r.corners[i - 1]), b = this.viewer.project(...r.corners[i]);
        const d = segDist(x, y, a[0], a[1], b[0], b[1]);
        if (d < bd) {
          bd = d;
          at = i;
        }
      }
    }
    if (at < r.corners.length) {
      // keep the hidden coordinate on the line so the new corner sits on it
      const a = r.corners[at - 1], b = r.corners[at], da = Math.hypot(pos[ax.u] - a[ax.u], pos[ax.v] - a[ax.v]), db = Math.hypot(pos[ax.u] - b[ax.u], pos[ax.v] - b[ax.v]);
      const t = da + db > 1e-9 ? da / (da + db) : 0.5;
      pos[ax.w] = a[ax.w] + (b[ax.w] - a[ax.w]) * t;
    }
    r.corners.splice(at, 0, pos);
    this.cornerSel = -1;
    this._respread(r);
    this._commit();
    this.say(r.corners.length < 2 ? "Now tap where the run goes next." : "");
  },

  _deleteCorner() {
    const r = this.cur;
    if (!r.corners || this.cornerSel < 0) return;
    r.corners.splice(this.cornerSel, 1);
    this.cornerSel = -1;
    if (r.corners.length) this._respread(r);
    this._commit();
  },

  // -- dots
  _dDot(x, y, e) {
    const r = this.cur, tch = isTouch(e);
    let hit = this._nearestDot(x, y, tch ? 28 : 18, r) || this._nearestDot(x, y, tch ? 28 : 18, null);
    if (!hit) {
      this._st = { k: "pan", x, y, panned: false };
      return true;
    }
    if (hit.run !== r) this.selectRun(hit.run, true);
    const rr = hit.run;
    this._st = { k: "dot", i: hit.i, base: new Float64Array(rr.P), s0: this.viewer.unproject(x, y, 0), x, y, moved: false, run: rr };
    this.focusPx = hit.i;
    this.viewer.invalidate();
    return true;
  },

  // -- select a range
  _dSelect(x, y, e) {
    const r = this.cur, tch = isTouch(e);
    const hit = this._nearestDot(x, y, tch ? 28 : 18, null);
    if (!hit) {
      this._st = { k: "clear", x, y, panned: false };
      return true;
    }
    if (hit.run !== r) {
      this._st = { k: "switch", hit, x, y, panned: false };
      return true;
    }
    this._st = { k: "range", from: hit.i, to: hit.i, x, y, moved: false, tapFrom: this._tapAnchor };
    return true;
  },

  // -- move / turn a whole run (or the selected pixels)
  _target() {
    const r = this.cur;
    return this.moveTarget === "sel" && this.sel ? [this.sel.a, this.sel.b] : [0, r.count - 1];
  },

  _rotHandle() {
    const r = this.cur, [a, b] = this._target();
    const ax = this._ax();
    if (!ax || !r) return null;
    const c = [0, 0, 0];
    for (let k = 0; k < 3; k++) c[k] = Geo.mean(r.P, a, b, k);
    const p = this.viewer.project(c[0], c[1], c[2]);
    const [, H] = this.viewer._size();
    const hy = p[1] - 66 < 22 ? p[1] + 66 : p[1] - 66;
    return { c, cx: p[0], cy: p[1], x: p[0], y: hy };
  },

  _dRun(x, y, e) {
    const r = this.cur, tch = isTouch(e), [a, b] = this._target();
    const rh = this._rotHandle();
    const base = new Float64Array(r.P), cb = r.corners ? r.corners.map((c) => [...c]) : null;
    if (rh && Math.hypot(x - rh.x, y - rh.y) <= (tch ? 34 : 22)) {
      this._st = { k: "rot", base, cb, a, b, pivot: rh.c, a0: Math.atan2(y - rh.cy, x - rh.cx), cx: rh.cx, cy: rh.cy, x, y, moved: false };
      return true;
    }
    const near = this._nearestDot(x, y, tch ? 28 : 18, r) || this._nearLine(x, y, r) <= (tch ? 24 : 12);
    if (near) {
      this._st = { k: "shift", base, cb, a, b, s0: this.viewer.unproject(x, y, 0), x, y, moved: false };
      return true;
    }
    const other = this._nearestDot(x, y, tch ? 28 : 18, null);
    this._st = other ? { k: "switch", hit: other, x, y, panned: false } : { k: "pan", x, y, panned: false };
    return true;
  },

  // -- drags in progress
  _mv(x, y, e, s) {
    const ax = this._ax(), r = this.cur;
    if (!s.moved && Math.hypot(x - s.x, y - s.y) < 5) return;
    s.moved = true;
    if (s.k === "corner") {
      const c = r.corners[s.i];
      let pos = this.viewer.unproject(x, y, c[ax.w]);
      pos = this._snapPos(pos, x, y, r, s.i);
      r.corners[s.i] = pos;
      this._respread(r);
      s.dirty = true;
    } else if (s.k === "range") {
      const hit = this._nearestDot(x, y, 60, r);
      if (hit) s.to = hit.i;
      this.sel = { a: Math.min(s.from, s.to), b: Math.max(s.from, s.to) };
      this.focusPx = s.to;
      this.viewer.invalidate();
    } else if (s.k === "dot") {
      const w = this.viewer.unproject(x, y, 0), d = [0, 0, 0];
      d[ax.u] = w[ax.u] - s.s0[ax.u];
      d[ax.v] = w[ax.v] - s.s0[ax.v];
      const P = s.run.P;
      P.set(s.base);
      const R = this.softR, n = s.run.count;
      for (let j = -R; j <= R; j++) {
        const i = s.i + j;
        if (i < 0 || i >= n) continue;
        const wt = Geo.falloff(j, R);
        for (let c = 0; c < 3; c++) P[i * 3 + c] = clamp(s.base[i * 3 + c] + d[c] * wt, 0, 1);
      }
      s.dirty = true;
      this._syncDots();
    } else if (s.k === "shift") {
      const w = this.viewer.unproject(x, y, 0), d = [0, 0, 0];
      d[ax.u] = w[ax.u] - s.s0[ax.u];
      d[ax.v] = w[ax.v] - s.s0[ax.v];
      this._shiftBy(s, d);
      s.dirty = true;
    } else if (s.k === "rot") {
      const th = Math.atan2(y - s.cy, x - s.cx) - s.a0;
      r.P.set(s.base);
      Geo.rotate(r.P, s.a, s.b, ax, s.pivot, th);
      Geo.clampAll(r.P, s.a, s.b);
      if (s.cb && s.a === 0 && s.b === r.count - 1) {
        const flat = new Float64Array(s.cb.flat());
        Geo.rotate(flat, 0, s.cb.length - 1, ax, s.pivot, th);
        r.corners = s.cb.map((_, i) => [clamp(flat[i * 3], 0, 1), clamp(flat[i * 3 + 1], 0, 1), clamp(flat[i * 3 + 2], 0, 1)]);
      }
      s.dirty = true;
      this._syncDots();
    }
  },

  // translate base positions by d (clamped so the run stays inside the room box); corners follow a whole-run move
  _shiftBy(s, d) {
    const r = this.cur;
    r.P.set(s.base);
    const [lo, hi] = Geo.bounds(s.base, s.a, s.b);
    const dd = [0, 1, 2].map((k) => clamp(d[k], -lo[k], 1 - hi[k]));
    for (let i = s.a; i <= s.b; i++) for (let k = 0; k < 3; k++) r.P[i * 3 + k] = s.base[i * 3 + k] + dd[k];
    if (s.cb && s.a === 0 && s.b === r.count - 1) r.corners = s.cb.map((c) => c.map((v, k) => clamp(v + dd[k], 0, 1)));
    this._syncDots();
  },

  _upSt(x, y, e, s) {
    const r = this.cur;
    if (s.k === "add") {
      if (!s.panned) this._addCorner(x, y);
      return;
    }
    if (s.k === "corner") {
      if (s.moved) this._commit();
      else this._renderPanel();
      return;
    }
    if (s.k === "pan") return;
    if (s.k === "clear") {
      if (!s.panned && this.sel) {
        this.sel = null;
        this._tapAnchor = null;
        this._renderPanel();
        this.viewer.invalidate();
      }
      return;
    }
    if (s.k === "switch") {
      if (!s.panned) {
        this.selectRun(s.hit.run, true);
        this.sel = { a: s.hit.i, b: s.hit.i };
        this.focusPx = s.hit.i;
        this._tapAnchor = s.hit.i;
        this._renderPanel();
        this._probe();
      }
      return;
    }
    if (s.k === "range") {
      if (!s.moved) {
        // tap: the first tap picks a pixel, the next tap picks the other end of a range
        if (s.tapFrom != null && s.tapFrom !== s.from) {
          this.sel = { a: Math.min(s.tapFrom, s.from), b: Math.max(s.tapFrom, s.from) };
          this._tapAnchor = null;
        } else {
          this.sel = { a: s.from, b: s.from };
          this._tapAnchor = s.from;
        }
        this.focusPx = s.from;
      } else this._tapAnchor = null;
      this._renderPanel();
      this.viewer.invalidate();
      this._probe();
      return;
    }
    if (s.k === "dot") {
      if (s.moved) {
        s.run.corners = null;
        if (s.run.kind === "traced") s.run.kind = "edited";
        s.run.fresh = false;
        this._commit();
      } else {
        this.sel = { a: s.i, b: s.i };
        this.viewer.invalidate();
        this._renderPanel();
        this._probe();
      }
      return;
    }
    if (s.k === "shift" || s.k === "rot") {
      if (s.moved) {
        if (!(s.cb && s.a === 0 && s.b === r.count - 1)) r.corners = null;
        if (!r.corners && r.kind === "traced") r.kind = "edited";
        r.fresh = false;
        this._commit();
      }
    }
  },
});

// ---- drawing on top of the dots: run paths, start arrows, corners, the selection --------------------------------------------
Object.assign(RunEditor.prototype, {
  _overlay(g, v, W, H) {
    const rgbOf = (r, a) => `rgba(${r.rgb[0]},${r.rgb[1]},${r.rgb[2]},${a})`;
    const P = (r, i) => v.project(r.P[i * 3], r.P[i * 3 + 1], r.P[i * 3 + 2]);
    g.lineJoin = "round";
    g.lineCap = "round";
    // the path of every run, the current one bold
    for (const r of this.runs) {
      const cur = r === this.cur, step = Math.max(1, Math.floor(r.count / 500));
      g.lineWidth = cur ? 1.8 : 1.3;
      g.strokeStyle = rgbOf(r, cur ? 0.6 : 0.4);
      g.beginPath();
      for (let i = 0; i < r.count; i += step) {
        const p = P(r, i);
        if (i) g.lineTo(p[0], p[1]);
        else g.moveTo(p[0], p[1]);
      }
      const e = P(r, r.count - 1);
      g.lineTo(e[0], e[1]);
      g.stroke();
    }
    // start arrows
    g.font = "600 12px system-ui, sans-serif";
    g.textAlign = "left";
    const boxes = [];
    for (const r of [...this.runs].sort((a, b) => (a === this.cur ? 1 : 0) - (b === this.cur ? 1 : 0)).reverse()) {
      const cur = r === this.cur, p0 = P(r, 0);
      let pk = null;
      for (let i = 1; i < r.count; i++) {
        const q = P(r, i);
        if (Math.hypot(q[0] - p0[0], q[1] - p0[1]) >= (cur ? 26 : 18)) {
          pk = q;
          break;
        }
      }
      const len = cur ? 17 : 11, wid = cur ? 8 : 5.5;
      g.globalAlpha = cur ? 1 : 0.75;
      if (pk) {
        const an = Math.atan2(pk[1] - p0[1], pk[0] - p0[0]), ca = Math.cos(an), sa = Math.sin(an);
        g.beginPath();
        g.moveTo(p0[0] + ca * len, p0[1] + sa * len);
        g.lineTo(p0[0] - ca * 3 - sa * wid, p0[1] - sa * 3 + ca * wid);
        g.lineTo(p0[0] - ca * 3 + sa * wid, p0[1] - sa * 3 - ca * wid);
        g.closePath();
        g.fillStyle = `rgb(${r.rgb.join(",")})`;
        g.fill();
        g.lineWidth = 1.6;
        g.strokeStyle = "#fff";
        g.stroke();
      }
      g.beginPath();
      g.arc(p0[0], p0[1], cur ? 6.5 : 4.5, 0, TAU);
      g.fillStyle = "#0b0d12";
      g.fill();
      g.lineWidth = 2.5;
      g.strokeStyle = `rgb(${r.rgb.join(",")})`;
      g.stroke();
      const label = cur ? `${r.name}  (start = pixel 0)` : r.name;
      const tw = g.measureText(label).width, lx = clamp(p0[0] + 12, 4, W - tw - 6), ly = clamp(p0[1] - 12, 14, H - 6);
      // other runs' names are skipped where they would sit on top of a name already drawn
      if (cur || !boxes.some((b) => lx < b[0] + b[2] && lx + tw > b[0] && ly - 12 < b[1] + 14 && ly + 2 > b[1])) {
        boxes.push([lx, ly - 12, tw]);
        g.fillStyle = "rgba(0,0,0,.65)";
        g.fillText(label, lx + 1, ly + 1);
        g.fillStyle = "#fff";
        g.fillText(label, lx, ly);
      }
      if (cur && r.count > 1) {
        const e = P(r, r.count - 1);
        g.fillStyle = `rgb(${r.rgb.join(",")})`;
        g.fillRect(e[0] - 5, e[1] - 5, 10, 10);
        g.strokeStyle = "#fff";
        g.lineWidth = 1.5;
        g.strokeRect(e[0] - 5, e[1] - 5, 10, 10);
      }
      g.globalAlpha = 1;
    }
    const r = this.cur;
    if (!r) return;
    // selected pixels
    if (this.sel) {
      const step = Math.max(1, Math.floor((this.sel.b - this.sel.a + 1) / 300));
      g.strokeStyle = "#fff";
      g.lineWidth = 1.4;
      for (let i = this.sel.a; i <= this.sel.b; i += step) {
        const p = P(r, i);
        g.beginPath();
        g.arc(p[0], p[1], 5.5, 0, TAU);
        g.stroke();
      }
      for (const [i, t] of this.sel.a === this.sel.b ? [[this.sel.a, String(this.sel.a)]] : [[this.sel.a, String(this.sel.a)], [this.sel.b, String(this.sel.b)]]) {
        const p = P(r, i);
        g.beginPath();
        g.arc(p[0], p[1], 9, 0, TAU);
        g.fillStyle = "rgba(255,255,255,.18)";
        g.fill();
        g.lineWidth = 2.5;
        g.stroke();
        const w = g.measureText(t).width;
        g.fillStyle = "rgba(0,0,0,.7)";
        g.fillRect(p[0] - w / 2 - 4, p[1] + 12, w + 8, 17);
        g.fillStyle = "#fff";
        g.fillText(t, p[0] - w / 2, p[1] + 25);
      }
    }
    // the pixel the probe lights
    if (this.probeOn && this.o.probe) {
      const a = this.probeMode === "sel" && this.sel ? this.sel.a : this.focusPx, b = this.probeMode === "sel" && this.sel ? this.sel.b : this.focusPx;
      for (let i = a; i <= b; i += Math.max(1, Math.floor((b - a + 1) / 200))) {
        const p = P(r, i);
        g.beginPath();
        g.arc(p[0], p[1], 8, 0, TAU);
        g.fillStyle = "rgba(255,200,0,.35)";
        g.fill();
        g.lineWidth = 2;
        g.strokeStyle = "#ffc400";
        g.stroke();
      }
    }
    if (this.view === "3d") return;
    // trace corners
    if (this.tool === "trace" && r.corners) {
      r.corners.forEach((c, i) => {
        const p = v.project(c[0], c[1], c[2]), on = i === this.cornerSel;
        g.beginPath();
        g.arc(p[0], p[1], 12, 0, TAU);
        g.fillStyle = on ? "#fff" : "#0b0d12";
        g.fill();
        g.lineWidth = 3;
        g.strokeStyle = `rgb(${r.rgb.join(",")})`;
        g.stroke();
        g.fillStyle = on ? "#0b0d12" : "#fff";
        g.font = "700 11px system-ui, sans-serif";
        g.textAlign = "center";
        g.fillText(String(i + 1), p[0], p[1] + 4);
        g.textAlign = "left";
      });
    }
    // move run: the turning handle
    if (this.tool === "run") {
      const rh = this._rotHandle();
      if (rh) {
        g.strokeStyle = "rgba(255,255,255,.55)";
        g.lineWidth = 1.5;
        g.setLineDash([4, 4]);
        g.beginPath();
        g.moveTo(rh.cx, rh.cy);
        g.lineTo(rh.x, rh.y);
        g.stroke();
        g.setLineDash([]);
        g.beginPath();
        g.arc(rh.cx, rh.cy, 4, 0, TAU);
        g.fillStyle = "#fff";
        g.fill();
        g.beginPath();
        g.arc(rh.x, rh.y, 17, 0, TAU);
        g.fillStyle = "#1976d2";
        g.fill();
        g.lineWidth = 2.5;
        g.strokeStyle = "#fff";
        g.stroke();
        g.beginPath();
        g.arc(rh.x, rh.y, 7, -2.6, 1.2);
        g.lineWidth = 2.5;
        g.stroke();
        g.beginPath();
        g.moveTo(rh.x + 7 * Math.cos(1.2) + 5, rh.y + 7 * Math.sin(1.2) - 3);
        g.lineTo(rh.x + 7 * Math.cos(1.2) - 3, rh.y + 7 * Math.sin(1.2) + 5);
        g.lineTo(rh.x + 7 * Math.cos(1.2) - 5, rh.y + 7 * Math.sin(1.2) - 4);
        g.fillStyle = "#fff";
        g.fill();
      }
    }
  },
});

// ---- the tool panel, edit buttons, save and the light-a-pixel probe ----------------------------------------------------------
Object.assign(RunEditor.prototype, {
  setTool(t) {
    if (!this.canEdit && t !== "select") return;
    this.tool = t;
    this.cornerSel = -1;
    this._st = null;
    if (this.view === "3d") this.setView("top", true);
    else this._renderPanel();
    this.viewer.invalidate();
  },

  _slider(label, min, max, step, value, fmt, oninput, onchange) {
    const w = this._el("div", "rte-sl");
    const id = "rte" + Math.random().toString(36).slice(2, 7);
    const l = this._el("label", "", label);
    l.htmlFor = id;
    const out = this._el("output", "", fmt(value));
    const inp = this._el("input");
    inp.type = "range";
    inp.id = id;
    inp.min = min;
    inp.max = max;
    inp.step = step;
    inp.value = value;
    inp.oninput = () => {
      out.textContent = fmt(Number(inp.value));
      oninput?.(Number(inp.value), inp);
    };
    inp.onchange = () => onchange?.(Number(inp.value), inp);
    w.append(l, out, inp);
    return w;
  },

  _row(...kids) {
    const r = this._el("div", "rte-row");
    r.append(...kids.filter(Boolean));
    return r;
  },

  _sect(title, ...kids) {
    const s = this._el("div", "");
    if (title) s.append(this._el("h4", "", title));
    const body = this._el("div", "");
    body.style.display = "grid";
    body.style.gap = "8px";
    body.append(...kids.filter(Boolean));
    s.append(body);
    return s;
  },

  _renderPanel() {
    const side = this.side;
    if (!side) return;
    const keep = this._pnl ? this._pnl.scrollTop : 0;
    side.textContent = "";
    const tools = this._el("div", "rte-tools");
    tools.setAttribute("role", "group");
    tools.setAttribute("aria-label", "Tools");
    for (const [id, t, ic] of [["trace", "Trace", ICO.trace], ["dot", "Move dots", ICO.dot], ["select", "Select", ICO.select], ["run", "Move run", ICO.run]]) {
      const b = this._b(t, "", () => this.setTool(id), ic);
      b.setAttribute("aria-pressed", String(this.tool === id));
      b.disabled = !this.canEdit && id !== "select";
      b.style.flexDirection = "column";
      tools.append(b);
    }
    const p = this._el("div", "rte-p");
    const r = this.cur;
    if (!r) {
      p.append(this._el("div", "hint", "There are no runs to edit in this area. Add a run with a pixel count first."));
    } else {
      const flat = this.view !== "3d";
      if (!this.canEdit) p.append(this._el("div", "hint", "Only an administrator can save changes to the map. You can look and light pixels."));
      if (this.tool === "trace") this._pTrace(p, r, flat);
      else if (this.tool === "dot") this._pDot(p, r, flat);
      else if (this.tool === "select") this._pSelect(p, r, flat);
      else this._pRun(p, r, flat);
    }
    side.append(tools, p);
    this._pnl = side;
    side.scrollTop = keep;
  },

  _status(r) {
    const k = r.kind;
    const what = k === "scan" ? "from a camera scan" : k === "traced" ? "traced as a line" : k === "imported" ? "imported" : k === "edited" ? "edited by hand" : r.fresh ? "on a default straight line (not mapped yet)" : "on a line";
    return `${r.name}: ${r.count} pixels, ${what}.`;
  },

  // -- Trace
  _pTrace(p, r, flat) {
    const cs = r.corners, nc = cs ? cs.length : 0;
    const top = this.view === "top";
    p.append(this._el("div", "hint", !flat ? "Switch to Top to trace." : top
      ? "Tap the plan to drop corner points along the run, in order from where pixel 0 starts. Drag a point to move it. Tap a segment to add a point inside it. Drag empty space to pan, pinch to zoom."
      : "Drag a point up or down to set its height. Switch to Top to add or move points around the room."));
    const info = this._el("div", "hint", this._status(r));
    if (nc >= 2) info.textContent += ` ${nc} corners, ${r.count} pixels spread evenly along ${Math.round(Geo.length(cs) * 100)}% of a room-width of line.`;
    else if (cs && !r.fresh && nc === 1) info.textContent += " One point so far. Tap where the run goes next.";
    p.append(info);
    if (cs && r.fresh) p.append(this._el("div", "hint", "This is only a default line. Tap empty space to start a new trace in its place, or drag its end points."));
    if (!cs && r.kind !== "traced") p.append(this._el("div", "hint", "Tapping starts a new traced line in place of this run's current positions. Undo brings them back."));
    // height
    const tgt = this.cornerSel >= 0 && cs?.[this.cornerSel] ? `corner ${this.cornerSel + 1}` : nc > 1 ? "the whole run" : "new points";
    const cur = this.cornerSel >= 0 && cs?.[this.cornerSel] ? cs[this.cornerSel][1] : nc ? cs.reduce((s, c) => s + c[1], 0) / nc : this.H;
    this.H = cur;
    const chips = this._row(...HEIGHTS.map(([n, v]) => {
      const b = this._b(n, "rte-b", () => {
        this._setHeight(v);
        this._commit();
      });
      b.append(this._el("small", "", pct(v)));
      b.disabled = !this.canEdit;
      b.setAttribute("aria-pressed", String(Math.abs(cur - v) < 0.006));
      return b;
    }));
    const sl = this._slider(`Height of ${tgt} (fraction of the room, floor to ceiling)`, 0, 100, 1, Math.round(cur * 100), (v) => v + "%", (v) => this._setHeight(v / 100), () => {
      this._commit();
    });
    sl.querySelector("input").disabled = !this.canEdit;
    let who = null;
    if (nc > 1) {
      const whole = this._b("Whole run", "rte-b", () => {
        this.cornerSel = -1;
        this._renderPanel();
        this.viewer.invalidate();
      });
      whole.setAttribute("aria-pressed", String(this.cornerSel < 0));
      const one = this._b(this.cornerSel >= 0 ? `Corner ${this.cornerSel + 1} only` : "One corner (tap it)", "rte-b", () => {});
      one.disabled = this.cornerSel < 0;
      one.setAttribute("aria-pressed", String(this.cornerSel >= 0));
      who = this._row(this._el("span", "hint", "Change the height of:"), whole, one);
    }
    p.append(this._sect("Height", who, chips, sl));
    const acts = this._row(
      this.cornerSel >= 0 && cs?.[this.cornerSel] ? this._b("Delete corner", "rte-b warn", () => this._deleteCorner()) : null,
      Object.assign(this._b("Start at the other end", "rte-b", () => this.flipDirection()), { disabled: !this.canEdit }),
      Object.assign(this._b("Start over", "rte-b warn", () => this.startOver()), { disabled: !this.canEdit }),
    );
    const grid = this._b("Snap to grid", "rte-b", () => {
      this.gridSnap = !this.gridSnap;
      this._renderPanel();
    });
    grid.setAttribute("aria-pressed", String(this.gridSnap));
    p.append(this._sect("", acts, this._row(grid, this._el("span", "hint", "Points also snap to the end of another run so they join up."))));
  },

  // target corner(s) get this height; with no corners yet it is the height of the next point
  _setHeight(v) {
    const r = this.cur;
    this.H = clamp(v, 0, 1);
    if (!r.corners || !r.corners.length) return;
    if (this.cornerSel >= 0 && r.corners[this.cornerSel]) r.corners[this.cornerSel][1] = this.H;
    else for (const c of r.corners) c[1] = this.H;
    this._respread(r);
  },

  flipDirection() {
    const r = this.cur;
    Geo.reverse(r.P);
    if (r.corners) r.corners.reverse();
    this.sel = null;
    this.cornerSel = -1;
    this.focusPx = 0;
    this._syncDots();
    this._commit();
    this.say("Pixel 0 is now at the other end.");
  },

  startOver() {
    const r = this.cur;
    r.corners = [];
    r.P.fill(0.5);
    r.kind = "traced";
    r.fresh = false;
    this.cornerSel = -1;
    this.sel = null;
    this._syncDots();
    this._commit();
    this.say("Cleared. Tap the plan to place the first corner.");
  },

  // -- Move dots
  _pDot(p, r, flat) {
    p.append(this._el("div", "hint", !flat ? "Switch to Top, Front or Side to move dots." : "Drag a dot. Top moves it across the room, Front and Side change its height. Tap a dot to pick it for the pixel light. Drag empty space to pan."));
    p.append(this._el("div", "hint", this._status(r)));
    const chips = this._row(...[["Just this dot", 0], ["A few", 6], ["Some", 20], ["Many", 60]].map(([t, v]) => {
      const b = this._b(t, "rte-b", () => {
        this.softR = v;
        this._renderPanel();
      });
      b.setAttribute("aria-pressed", String(this.softR === v));
      return b;
    }));
    p.append(this._sect("Bring neighbouring pixels along", chips));
    this._probeBlock(p, r);
  },

  // -- Select
  _pSelect(p, r, flat) {
    const n = r.count, s = this.sel;
    p.append(this._el("div", "hint", !flat ? "Switch to Top, Front or Side to pick pixels." : "Tap a dot, then tap another dot to pick the pixels between them. Or drag along the run. Tap empty space to clear."));
    const [a, b] = s ? [s.a, s.b] : [0, n - 1];
    p.append(this._el("div", "hint", s ? `Picked pixels ${a} to ${b} (${b - a + 1}).` : `Nothing picked, so the buttons below use the whole run (${n} pixels).`));
    const from = this._slider("From pixel", 0, n - 1, 1, a, (v) => String(v), (v, inp) => this._setRange(v, null), () => this._renderPanel());
    const to = this._slider("To pixel", 0, n - 1, 1, b, (v) => String(v), (v) => this._setRange(null, v), () => this._renderPanel());
    from.querySelector("input").disabled = to.querySelector("input").disabled = false;
    const sr = this._row(
      this._b("Pick the whole run", "rte-b", () => {
        this.sel = { a: 0, b: n - 1 };
        this._renderPanel();
        this.viewer.invalidate();
      }),
      Object.assign(this._b("Clear", "rte-b", () => {
        this.sel = null;
        this._tapAnchor = null;
        this._renderPanel();
        this.viewer.invalidate();
      }), { disabled: !s }),
    );
    p.append(this._sect("Range", sr, from, to));
    const e = this.canEdit;
    const shape = this._row(
      Object.assign(this._b("Straighten", "rte-b", () => this._edit("straighten")), { disabled: !e || b - a < 2, title: "Make these pixels a straight, evenly spaced line between the first and last" }),
      Object.assign(this._b("Even out", "rte-b", () => this._edit("even")), { disabled: !e || b - a < 2, title: "Space these pixels evenly along the path they already follow" }),
      Object.assign(this._b("Smooth", "rte-b", () => this._edit("smooth")), { disabled: !e || b - a < 2, title: "Round off kinks. Press again for more." }),
    );
    p.append(this._sect("Shape the picked pixels", shape));
    const hts = this._row(...HEIGHTS.map(([nm, v]) => {
      const bt = this._b(nm, "rte-b", () => this._snapTo(1, v));
      bt.append(this._el("small", "", pct(v)));
      bt.disabled = !e;
      return bt;
    }));
    // snap to a plane: height, left-right or front-back
    const key = [r.id, a, b, this.snapAxis].join();
    if (this._snapKey !== key) {
      this._snapKey = key;
      this.snapV = Geo.mean(r.P, a, b, this.snapAxis);
    }
    const axes = this._row(...[["Height", 1], ["Left-right", 0], ["Front-back", 2]].map(([t, ax]) => {
      const bt = this._b(t, "rte-b", () => {
        this.snapAxis = ax;
        this._renderPanel();
      });
      bt.setAttribute("aria-pressed", String(this.snapAxis === ax));
      return bt;
    }));
    const val = this._slider("Level", 0, 100, 1, Math.round(this.snapV * 100), (v) => v + "%", (v) => {
      this.snapV = v / 100;
    });
    val.querySelector("input").disabled = !e;
    const go = Object.assign(this._b("Flatten onto this plane", "rte-b pri", () => this._snapTo(this.snapAxis, this.snapV)), { disabled: !e });
    p.append(this._sect("Snap the picked pixels to a height", hts), this._sect("Or flatten onto a plane", axes, val, go));
    this._probeBlock(p, r);
  },

  _setRange(a, b) {
    const n = this.cur.count;
    let A = a ?? this.sel?.a ?? 0, B = b ?? this.sel?.b ?? n - 1;
    if (a != null && A > B) B = A;
    if (b != null && B < A) A = B;
    this.sel = { a: A, b: B };
    this.focusPx = a != null ? A : B;
    this.viewer.invalidate();
    this._probe();
  },

  _range() {
    return this.sel ? [this.sel.a, this.sel.b] : [0, this.cur.count - 1];
  },

  _edit(what) {
    const r = this.cur, [a, b] = this._range(), whole = a === 0 && b === r.count - 1;
    if (what === "straighten") {
      Geo.straighten(r.P, a, b);
      if (whole) {
        r.corners = [[...r.P.slice(0, 3)], [...r.P.slice((r.count - 1) * 3, r.count * 3)]];
        r.kind = "traced";
      } else r.corners = null;
    } else if (what === "even") {
      Geo.even(r.P, a, b);
      r.corners = null;
    } else if (what === "smooth") {
      Geo.smooth(r.P, a, b, 6, 0.5);
      r.corners = null;
    }
    if (!r.corners && r.kind === "traced") r.kind = "edited";
    r.fresh = false;
    Geo.clampAll(r.P, 0, r.count - 1);
    this._syncDots();
    this._commit();
  },

  _snapTo(axis, v) {
    const r = this.cur, [a, b] = this._range();
    Geo.snap(r.P, a, b, axis, clamp(v, 0, 1));
    r.corners = null;
    if (r.kind === "traced") r.kind = "edited";
    r.fresh = false;
    this._syncDots();
    this._commit();
    this.say(`Snapped to ${axis === 1 ? "height" : axis === 0 ? "left-right" : "front-back"} ${pct(v)}.`);
  },

  // -- Move run
  _pRun(p, r, flat) {
    p.append(this._el("div", "hint", !flat ? "Switch to Top, Front or Side to move a run." : "Drag the run to move it. Drag the round blue handle to turn it. Drag empty space to pan, pinch to zoom."));
    const sel = this.sel && !(this.sel.a === 0 && this.sel.b === r.count - 1);
    if (!sel) this.moveTarget = "run";
    const tg = this._row(
      Object.assign(this._b("Whole run", "rte-b", () => {
        this.moveTarget = "run";
        this._renderPanel();
        this.viewer.invalidate();
      })),
      Object.assign(this._b(sel ? `Picked pixels (${this.sel.a}-${this.sel.b})` : "Picked pixels (pick some in Select)", "rte-b", () => {
        this.moveTarget = "sel";
        this._renderPanel();
        this.viewer.invalidate();
      }), { disabled: !sel }),
    );
    tg.children[0].setAttribute("aria-pressed", String(this.moveTarget === "run"));
    tg.children[1].setAttribute("aria-pressed", String(this.moveTarget === "sel"));
    p.append(this._sect("Move or turn", tg));
    const step = this._row(...[["Fine", 0.005], ["Medium", 0.02], ["Coarse", 0.05]].map(([t, v]) => {
      const b = this._b(t, "rte-b", () => {
        this.nudgeStep = v;
        this._renderPanel();
      });
      b.append(this._el("small", "", pct(v)));
      b.setAttribute("aria-pressed", String(this.nudgeStep === v));
      return b;
    }));
    const pad = this._el("div", "rte-pad");
    const nb = (t, d) => Object.assign(this._b(t, "rte-b", () => this._nudge(d)), { disabled: !this.canEdit });
    pad.append(nb("Left", [-1, 0, 0]), nb("Higher", [0, 1, 0]), nb("Right", [1, 0, 0]), nb("Towards back", [0, 0, -1]), nb("Lower", [0, -1, 0]), nb("Towards front", [0, 0, 1]));
    p.append(this._sect("Nudge", step, pad));
    const turn = this._row(...[["Turn left 15", -15], ["Turn right 15", 15], ["Turn left 90", -90], ["Turn right 90", 90]].map(([t, d]) => Object.assign(this._b(t + "°", "rte-b", () => this._turn(d)), { disabled: !this.canEdit })));
    const mir = this._row(
      Object.assign(this._b("Mirror left-right", "rte-b", () => this._mirror(0)), { disabled: !this.canEdit }),
      Object.assign(this._b("Mirror front-back", "rte-b", () => this._mirror(2)), { disabled: !this.canEdit }),
      Object.assign(this._b("Start at the other end", "rte-b", () => this.flipDirection()), { disabled: !this.canEdit }),
    );
    p.append(this._sect("Turn about the vertical (seen from above)", turn), this._sect("Mirror and direction", mir));
  },

  _applyXf(fn) {
    const r = this.cur, [a, b] = this._target(), whole = a === 0 && b === r.count - 1;
    fn(r.P, a, b);
    Geo.clampAll(r.P, a, b);
    if (r.corners && whole) {
      const flat = new Float64Array(r.corners.flat());
      fn(flat, 0, r.corners.length - 1);
      r.corners = r.corners.map((_, i) => [clamp(flat[i * 3], 0, 1), clamp(flat[i * 3 + 1], 0, 1), clamp(flat[i * 3 + 2], 0, 1)]);
    } else r.corners = null;
    if (!r.corners && r.kind === "traced") r.kind = "edited";
    r.fresh = false;
    this._syncDots();
    this._commit();
  },

  _nudge(d) {
    const r = this.cur, [a, b] = this._target();
    const [lo, hi] = Geo.bounds(r.P, a, b), s = this.nudgeStep;
    const dd = d.map((v, k) => clamp(v * s, -lo[k], 1 - hi[k]));
    if (!dd.some((v) => Math.abs(v) > 1e-9)) return this.say("That is as far as the room goes.");
    this._applyXf((P, i0, i1) => {
      for (let i = i0; i <= i1; i++) for (let k = 0; k < 3; k++) P[i * 3 + k] += dd[k];
    });
  },

  _turn(deg) {
    const r = this.cur, [a, b] = this._target(), pv = [0, 1, 2].map((k) => Geo.mean(r.P, a, b, k));
    this._applyXf((P, i0, i1) => Geo.rotate(P, i0, i1, VIEW_AXES.top, pv, (deg * Math.PI) / 180));
  },

  _mirror(axis) {
    const r = this.cur, [a, b] = this._target(), [lo, hi] = Geo.bounds(r.P, a, b), m = lo[axis] + hi[axis];
    this._applyXf((P, i0, i1) => {
      for (let i = i0; i <= i1; i++) P[i * 3 + axis] = m - P[i * 3 + axis];
    });
  },

  // -- light a pixel (map/scan/start + map/scan/segment, see the panel)
  _probeBlock(p, r) {
    const pr = this.o.probe;
    if (!pr) return;
    const can = pr.can ? pr.can(r.raw) : { ok: true };
    const on = this.probeOn && can.ok;
    const tg = this._b(on ? "Stop lighting" : "Light the pixel I pick", "rte-b" + (on ? " pri" : ""), () => {
      if (on) {
        this.probeOn = false;
        pr.stop?.();
        this.say("");
      } else {
        this.probeOn = true;
        this._probe();
      }
      this._renderPanel();
      this.viewer.invalidate();
    }, ICO.bulb);
    tg.disabled = !can.ok || !this.canEdit;
    const kids = [tg];
    if (on) {
      const seg = this._row(...[["The pixel", "pixel"], ["The picked range", "sel"]].map(([t, m]) => {
        const b = this._b(t, "rte-b", () => {
          this.probeMode = m;
          this._probe();
          this._renderPanel();
        });
        b.setAttribute("aria-pressed", String(this.probeMode === m));
        b.disabled = m === "sel" && !this.sel;
        return b;
      }));
      const step = this._row(...[["-10", -10], ["-1", -1], ["+1", 1], ["+10", 10]].map(([t, d]) => {
        const b = this._b(t, "rte-b", () => this._step(d));
        b.setAttribute("aria-label", `Light pixel ${d > 0 ? "+" : ""}${d} from this one`);
        return b;
      }));
      kids.push(seg, this._el("div", "hint", `Pixel ${this.focusPx} of ${r.count}. Step to the next pixel to see where the strip actually goes.`), step);
    }
    if (!can.ok) kids.push(this._el("div", "hint", can.why || "This light can't light single pixels."));
    else kids.push(this._el("div", "hint", "Lighting a pixel borrows that light until you stop (it goes back to what it showed)."));
    p.append(this._sect("Where is this pixel really?", ...kids));
  },

  _step(d) {
    const r = this.cur;
    const f = clamp(this.focusPx + d, 0, r.count - 1);
    this.focusPx = f;
    this.sel = { a: f, b: f };
    this.viewer.invalidate();
    this._renderPanel();
    this._probe();
  },

  _probe() {
    const pr = this.o.probe;
    if (!this.probeOn || !pr || !this.cur) return;
    clearTimeout(this._probeT);
    this._probeT = setTimeout(async () => {
      const r = this.cur;
      let i0 = this.focusPx, n = 1;
      if (this.probeMode === "sel" && this.sel) {
        i0 = this.sel.a;
        n = this.sel.b - this.sel.a + 1;
      }
      try {
        await pr.light(r.raw, i0, n);
        this.say(`Lighting pixel ${i0}${n > 1 ? " to " + (i0 + n - 1) : ""} of ${r.name}.`);
      } catch (err) {
        this.probeOn = false;
        this.say(String(err?.message || err || "Couldn't light that pixel."), true);
        this._renderPanel();
        this.viewer.invalidate();
      }
    }, 140);
  },

  // -- save
  async save() {
    if (!this.dirty || this._saving || !this.canEdit) return;
    const changed = this.runs.filter((r) => this._changed(r));
    const bad = changed.find((r) => r.corners && r.corners.length < 2);
    if (bad) {
      this.say(`${bad.name} needs at least two points. Tap the plan to place them.`, true);
      this.selectRun(bad);
      this.setTool("trace");
      return;
    }
    const results = changed.map((r) => ({ id: r.id, positions: this._ints(r), pts: this._outPts(r) }));
    this._saving = true;
    this._renderHead();
    let err = "";
    try {
      err = (await this.o.onSave(results)) || "";
    } catch (e) {
      err = String(e?.message || e || "Couldn't save the map.");
    }
    this._saving = false;
    if (err) this.say(err, true);
    else {
      this.savedId = this.hist[this.hi].id;
      for (const r of changed) r.orig = this._key(r);
      this.say("Saved.");
    }
    if (this.root.isConnected) {
      this._renderHead();
      this._renderChips();
    }
  },
});
