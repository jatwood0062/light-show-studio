// Light Show Studio: the 3D map viewer. A small canvas renderer (no libraries) used by the Map tab's area cards, the area
// view and the scan wizard. World space is a unit box: x right, y UP, z towards the viewer (docs/MAPPING_V2.md).
//
//   const v = new Viewer3D(canvas, { interactive: true, spin: 0, fit: false, onEdit, onEditEnd, onTap, onMode });
//   v.setDots({ n, pos: Float32Array(3n), base: Uint8ClampedArray(3n), groups: [{ start, len, rgb }], flags: Uint8Array(n) });
//   v.setLive(Uint8ClampedArray(3n) | null, Uint8Array(n) | null)    // live colours (lit = 1)
//   v.setHandles([{ id, pos: [x, y, z], rgb: [r, g, b], label, big }]);   // draggable end points (front/side view only)
//   v.setMode("3d" | "front" | "side");  v.reset();  v.destroy();
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
    const s = (f * d) / Math.max(0.2, d - z2);
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
    const dimOf = this._dimOf || null;
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
      const front = this.mode === "front";
      for (let i = 0; i <= 10; i++) {
        const t = i / 10;
        if (front) {
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
      if (front) {
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
        this.zoom = clamp(this.zoom * (k === "-" ? 0.85 : 1.18), 0.5, 8);
        this.invalidate();
        e.preventDefault();
      } else if (k === "0" || k === "Escape") this.reset();
    });
  }

  _zoomAt(f, x, y) {
    const old = this.zoom;
    this.zoom = clamp(old * f, 0.5, 8);
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
    if (this.mode === "front") {
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
