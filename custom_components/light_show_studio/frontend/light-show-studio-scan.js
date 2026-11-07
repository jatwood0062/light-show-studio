// Light Show Studio: camera-scan maths. Pure functions, no DOM, so they run in the browser and in node (tests/js/test_scan.mjs).
// Spec: docs/MAPPING_V2.md section 3. Frame k of a scan: 0 = all off (dark reference), 1 = all on (dim white), then for each
// bit b of the pixel index two frames: 2+2b lights the pixels whose bit b is set, 3+2b lights the others (the inverse).

export const SYNC = { blinks: 3, onMs: 400, offMs: 400, gapMs: 1200 }; // video fallback: sync blinks, then the code frames

// number of index bits for n pixels, and the frame count
export function scanBits(n) {
  let b = 1;
  while (1 << b < n) b++;
  return b;
}
export const scanFrames = (n) => 2 + 2 * scanBits(n);

// 0 = off, 1 = dim all-on, 2 = lit, for pixel i in frame k
export function frameLit(i, k) {
  if (k === 0) return 0;
  if (k === 1) return 1;
  const b = (k - 2) >> 1, inv = (k - 2) & 1;
  return (((i >> b) & 1) ^ inv) === 1 ? 2 : 0;
}

// when each frame starts (ms after the first sync blink) in the video fallback
export function videoFrameStart(k, hold) {
  return SYNC.blinks * (SYNC.onMs + SYNC.offMs) + SYNC.gapMs + k * hold;
}

// RGBA bytes -> luma bytes
export function lumaFromRGBA(rgba, w, h) {
  const out = new Uint8ClampedArray(w * h);
  for (let i = 0, j = 0; i < out.length; i++, j += 4) out[i] = (rgba[j] * 77 + rgba[j + 1] * 150 + rgba[j + 2] * 29) >> 8;
  return out;
}

// ---- blobs -------------------------------------------------------------------------------------------------------
function percentile(arr, p) {
  const hist = new Uint32Array(256);
  for (let i = 0; i < arr.length; i++) hist[arr[i]]++;
  let target = Math.floor(arr.length * p), acc = 0;
  for (let v = 0; v < 256; v++) {
    acc += hist[v];
    if (acc > target) return v;
  }
  return 255;
}

function smooth121(src, w, h) {
  const tmp = new Uint16Array(w * h), out = new Uint8ClampedArray(w * h);
  for (let y = 0; y < h; y++) {
    const r = y * w;
    for (let x = 0; x < w; x++) {
      const a = src[r + (x > 0 ? x - 1 : x)], b = src[r + x], c = src[r + (x < w - 1 ? x + 1 : x)];
      tmp[r + x] = a + 2 * b + c;
    }
  }
  for (let y = 0; y < h; y++) {
    const y0 = (y > 0 ? y - 1 : y) * w, y1 = y * w, y2 = (y < h - 1 ? y + 1 : y) * w;
    for (let x = 0; x < w; x++) out[y1 + x] = (tmp[y0 + x] + 2 * tmp[y1 + x] + tmp[y2 + x]) >> 4;
  }
  return out;
}

// bright local maxima of (allOn - dark): [{x, y, px, py, v}] strongest first, at least minDist apart
export function findBlobs(dark, allOn, w, h, opts = {}) {
  const diff = new Uint8ClampedArray(w * h);
  for (let i = 0; i < diff.length; i++) diff[i] = Math.max(0, allOn[i] - dark[i]);
  const sm = opts.smooth ? smooth121(diff, w, h) : diff;
  const ref = percentile(diff, 0.9995);
  const thr = Math.max(opts.minDiff ?? 14, (opts.fraction ?? 0.25) * ref);
  const minDist = opts.minDist ?? Math.max(2, Math.round(w / 1200));
  const cand = [];
  for (let y = 1; y < h - 1; y++) {
    const r = y * w;
    for (let x = 1; x < w - 1; x++) {
      const v = sm[r + x];
      if (v < thr) continue;
      // strictly above the neighbours that come earlier in raster order, at least equal to the later ones
      if (v <= sm[r + x - 1] || v <= sm[r - w + x - 1] || v <= sm[r - w + x] || v <= sm[r - w + x + 1]) continue;
      if (v < sm[r + x + 1] || v < sm[r + w + x - 1] || v < sm[r + w + x] || v < sm[r + w + x + 1]) continue;
      cand.push([v, x, y]);
    }
  }
  cand.sort((a, b) => b[0] - a[0]);
  // non-maximum suppression on a coarse grid
  const cell = Math.max(2, minDist), gw = Math.ceil(w / cell) + 1;
  const grid = new Map();
  const out = [];
  const d2 = minDist * minDist;
  for (const [v, x, y] of cand) {
    const gx = Math.floor(x / cell), gy = Math.floor(y / cell);
    let ok = true;
    for (let j = gy - 1; j <= gy + 1 && ok; j++) {
      for (let i = gx - 1; i <= gx + 1 && ok; i++) {
        const list = grid.get(j * gw + i);
        if (!list) continue;
        for (const o of list) {
          if ((o.px - x) ** 2 + (o.py - y) ** 2 < d2) {
            ok = false;
            break;
          }
        }
      }
    }
    if (!ok) continue;
    // sub-pixel centre from the un-smoothed difference around the peak
    let sx = 0, sy = 0, sw = 0;
    for (let dy = -1; dy <= 1; dy++) {
      for (let dx = -1; dx <= 1; dx++) {
        const wgt = Math.max(0, diff[(y + dy) * w + x + dx] - thr * 0.5);
        sx += wgt * (x + dx);
        sy += wgt * (y + dy);
        sw += wgt;
      }
    }
    const blob = { x: sw ? sx / sw : x, y: sw ? sy / sw : y, px: x, py: y, v };
    out.push(blob);
    const key = gy * gw + gx;
    if (!grid.has(key)) grid.set(key, []);
    grid.get(key).push(blob);
    if (out.length >= (opts.maxBlobs ?? 6000)) break;
  }
  return out;
}

// ---- decoding ----------------------------------------------------------------------------------------------------
// frames: array of luma arrays (frame k as in the header comment), all w x h. Returns {points, found, ...}; points[i] is
// [x, y, confidence] in pixels (y down, like the image) or null.
export function decodeScan(frames, n, w, h, opts = {}) {
  const B = scanBits(n), F = 2 + 2 * B;
  if (frames.length < F) throw new Error(`Expected ${F} frames, got ${frames.length}.`);
  const dark = frames[0];
  const blobs = findBlobs(dark, frames[1], w, h, opts);
  const val = (k, b) => Math.max(0, frames[k][b.py * w + b.px] - dark[b.py * w + b.px]);
  const A = [], C = [];
  const med = new Array(B);
  for (let b = 0; b < B; b++) {
    const a = blobs.map((bl) => val(2 + 2 * b, bl)), c = blobs.map((bl) => val(3 + 2 * b, bl));
    A.push(a);
    C.push(c);
    const mx = a.map((v, i) => Math.max(v, c[i])).sort((p, q) => p - q);
    med[b] = mx.length ? mx[mx.length >> 1] : 0;
  }
  const rej = { badBits: 0, outOfRange: 0, duplicate: 0 };
  const points = new Array(n).fill(null);
  const minRatio = opts.minContrast ?? 0.45;
  blobs.forEach((bl, bi) => {
    let idx = 0, conf = 1;
    for (let b = 0; b < B; b++) {
      const a = A[b][bi], c = C[b][bi], hi = Math.max(a, c), lo = Math.min(a, c);
      if (hi < Math.max(10, 0.3 * med[b]) || lo > (1 - minRatio) * hi) {
        rej.badBits++;
        return;
      }
      if (a > c) idx |= 1 << b;
      conf = Math.min(conf, (hi - lo) / hi);
    }
    if (idx >= n) {
      rej.outOfRange++;
      return;
    }
    const old = points[idx];
    if (old && old[2] >= conf) {
      rej.duplicate++;
      return;
    }
    if (old) rej.duplicate++;
    points[idx] = [bl.x, bl.y, conf];
  });
  return { points, found: points.filter(Boolean).length, blobs: blobs.length, rejected: rej, w, h };
}

// ---- filling gaps, normalising -------------------------------------------------------------------------------------------
// Missing pixels are placed along the string order: between two found ones in a straight line, past the ends by the local
// step. Returns {x, y, est} where est[i] = 1 for filled-in pixels. Input points[i] = [x, y] or null.
export function fillMissing(points) {
  const n = points.length;
  const x = new Float64Array(n), y = new Float64Array(n), est = new Uint8Array(n);
  const known = [];
  for (let i = 0; i < n; i++) {
    if (points[i]) {
      x[i] = points[i][0];
      y[i] = points[i][1];
      known.push(i);
    } else est[i] = 1;
  }
  if (!known.length) return { x, y, est, found: 0 };
  for (let k = 0; k + 1 < known.length; k++) {
    const a = known[k], b = known[k + 1];
    for (let i = a + 1; i < b; i++) {
      const t = (i - a) / (b - a);
      x[i] = x[a] + (x[b] - x[a]) * t;
      y[i] = y[a] + (y[b] - y[a]) * t;
    }
  }
  const edge = (from, dir, nearest) => {
    // step per pixel over the nearest few found pixels
    const m = Math.min(nearest.length, 6);
    let dx = 0, dy = 0;
    if (m >= 2) {
      const a = nearest[0], b = nearest[m - 1];
      dx = (x[b] - x[a]) / (b - a);
      dy = (y[b] - y[a]) / (b - a);
    }
    for (let i = from, j = 1; i >= 0 && i < n; i += dir, j++) {
      if (!est[i]) break;
      const steps = Math.min(j, 12);
      x[i] = x[nearest[0]] + dx * dir * steps;
      y[i] = y[nearest[0]] + dy * dir * steps;
    }
  };
  const first = known[0], last = known[known.length - 1];
  if (first > 0) edge(first - 1, -1, known.slice(0, 6));
  if (last < n - 1) edge(last + 1, 1, known.slice(-6).reverse());
  return { x, y, est, found: known.length };
}

// pixels (y down) -> y-up numbers; returns {x, y} scaled by 1/ext about the centre of the found points
export function toWorld(fill, opts = {}) {
  const n = fill.x.length;
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (let i = 0; i < n; i++) {
    if (fill.est[i]) continue;
    x0 = Math.min(x0, fill.x[i]);
    x1 = Math.max(x1, fill.x[i]);
    y0 = Math.min(y0, -fill.y[i]);
    y1 = Math.max(y1, -fill.y[i]);
  }
  if (!Number.isFinite(x0)) return { x: new Float64Array(n), y: new Float64Array(n), est: fill.est, ext: 1, cx: 0, cy: 0, hy: 1 };
  const ext = Math.max(x1 - x0, y1 - y0, 1e-6);
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  const x = new Float64Array(n), y = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    x[i] = (fill.x[i] - cx) / ext;
    y[i] = (-fill.y[i] - cy) / ext;
  }
  return { x, y, est: fill.est, ext, hy: (y1 - y0) / ext };
}

// least squares y_front = a * y_side + b over indexes found in both views, one trimming pass
export function alignY(front, side) {
  const n = Math.min(front.y.length, side.y.length);
  let idx = [];
  for (let i = 0; i < n; i++) if (!front.est[i] && !side.est[i]) idx.push(i);
  if (idx.length < 8) return { a: 1, b: 0, used: idx.length, ok: false };
  const fit = (list) => {
    let sx = 0, sy = 0, sxx = 0, sxy = 0;
    for (const i of list) {
      sx += side.y[i];
      sy += front.y[i];
      sxx += side.y[i] ** 2;
      sxy += side.y[i] * front.y[i];
    }
    const m = list.length, den = m * sxx - sx * sx;
    if (Math.abs(den) < 1e-9) return null;
    const a = (m * sxy - sx * sy) / den;
    return { a, b: (sy - a * sx) / m };
  };
  let f = fit(idx);
  if (!f) return { a: 1, b: 0, used: idx.length, ok: false };
  const res = idx.map((i) => Math.abs(front.y[i] - (f.a * side.y[i] + f.b)));
  const sorted = [...res].sort((p, q) => p - q);
  const cut = Math.max(sorted[Math.floor(sorted.length * 0.8)], 0.01);
  const keep = idx.filter((_, k) => res[k] <= cut);
  if (keep.length >= 8) f = fit(keep) || f;
  const ok = f.a > 0.3 && f.a < 3;
  return { a: ok ? f.a : 1, b: ok ? f.b : 0, used: keep.length, ok };
}

// Final positions in 0-1: x from the front, y from the front, z from the side (or 0.5). world = {x, y, est} from toWorld.
// flipZ flips the depth direction. Returns {pos: Float32Array(3n), est}.
export function mergeViews(front, side, opts = {}) {
  const n = front.x.length;
  const X = Float64Array.from(front.x), Y = Float64Array.from(front.y), Z = new Float64Array(n);
  let aligned = null;
  if (side) {
    aligned = alignY(front, side);
    const s = opts.flipZ ? 1 : -1; // side camera to the right of the lights looking left: image right is away from the viewer
    for (let i = 0; i < n; i++) Z[i] = s * aligned.a * (i < side.x.length ? side.x[i] : 0);
  }
  return { ...fitUnitBox(X, Y, Z, front.est, !!side), aligned };
}

// centre each axis on 0.5 and scale all by the same factor so the biggest extent spans margin of the box
export function fitUnitBox(X, Y, Z, est, hasZ, margin = 0.9) {
  const n = X.length;
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < n; i++) {
    for (const [k, arr] of [[0, X], [1, Y], [2, Z]]) {
      lo[k] = Math.min(lo[k], arr[i]);
      hi[k] = Math.max(hi[k], arr[i]);
    }
  }
  const ext = Math.max(hi[0] - lo[0], hi[1] - lo[1], hasZ ? hi[2] - lo[2] : 0, 1e-9);
  const k = margin / ext;
  const pos = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    pos[i * 3] = 0.5 + (X[i] - (lo[0] + hi[0]) / 2) * k;
    pos[i * 3 + 1] = 0.5 + (Y[i] - (lo[1] + hi[1]) / 2) * k;
    pos[i * 3 + 2] = hasZ ? 0.5 + (Z[i] - (lo[2] + hi[2]) / 2) * k : 0.5;
  }
  return { pos, est, k, c: [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2] };
}

// ---- video sync -----------------------------------------------------------------------------------------------------------
// samples: [{t (s), v (brightness)}] in time order. Finds the first run of SYNC.blinks flashes and returns the time (s) of the
// first one's rising edge, or null. The returned estimate lies between two samples; `bracket` is [t before, t after].
export function findSync(samples, sync = SYNC) {
  if (samples.length < 6) return null;
  const vs = samples.map((s) => s.v).sort((a, b) => a - b);
  const lo = vs[Math.floor(vs.length * 0.1)], hi = vs[Math.min(vs.length - 1, Math.floor(vs.length * 0.98))];
  if (hi - lo < 4) return null;
  const thr = lo + 0.5 * (hi - lo);
  const edges = [];
  for (let i = 1; i < samples.length; i++) {
    if (samples[i - 1].v < thr && samples[i].v >= thr) {
      const t0 = samples[i - 1].t, t1 = samples[i].t;
      const f = (thr - samples[i - 1].v) / (samples[i].v - samples[i - 1].v || 1);
      edges.push({ t: t0 + (t1 - t0) * f, bracket: [t0, t1] });
    }
  }
  const period = (sync.onMs + sync.offMs) / 1000, tol = Math.max(0.3, period * 0.3);
  for (let i = 0; i < edges.length; i++) {
    let ok = true;
    for (let k = 1; k < sync.blinks && ok; k++) ok = edges.some((e) => Math.abs(e.t - edges[i].t - k * period) <= tol);
    if (ok) return edges[i];
  }
  return null;
}

// the brightness of a frame for findSync: the mean of its brightest 2% of pixels (a lit string in a dark room)
export function topBrightness(luma) {
  const hist = new Uint32Array(256);
  for (let i = 0; i < luma.length; i++) hist[luma[i]]++;
  let need = Math.max(1, Math.floor(luma.length * 0.02)), sum = 0;
  for (let v = 255; v >= 0 && need > 0; v--) {
    const take = Math.min(need, hist[v]);
    sum += take * v;
    need -= take;
  }
  return sum / Math.max(1, Math.floor(luma.length * 0.02));
}

// ======================================================================================================================
// Glow scan (hidden strips). For lights the camera never sees as points (under cabinets, behind a cove): one short lit
// stretch ("anchor") at a time is stepped along a run, with a dark frame for reference. Each anchor lights a diffuse puddle
// somewhere; its brightness-weighted centre is the anchor's place, and the pixels in between are interpolated along the
// string. Everything below is pure (no DOM), so it runs in node too (tests/js/test_glow.mjs).
// Camera frames are shrunk first (downscaleLuma) so ~80 of them fit in memory and every search is fast; coordinates come
// back in full-size camera pixels (y down), like the point scan's.
// ======================================================================================================================
export const GLOW = { minAnchors: 16, maxAnchors: 24, minWidth: 6, maxWidth: 10, darkEvery: 12, minConf: 0.35, smallWidth: 480 };

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

// Which stretches to light for a run of `count` pixels: [{i, start, width, c}], start relative to the run, c = the stretch's
// centre pixel (a fraction when the width is even). Default ~16-24 anchors, 6-10 pixels wide; opts {anchors, width}.
export function glowPlan(count, opts = {}) {
  const n = Math.max(1, Math.floor(count));
  let A = opts.anchors ?? Math.min(GLOW.maxAnchors, Math.max(GLOW.minAnchors, Math.round(1.2 * Math.sqrt(n))));
  A = clamp(Math.round(A), 1, n);
  let width = opts.width ?? clamp(Math.round((0.6 * n) / A), GLOW.minWidth, GLOW.maxWidth);
  width = clamp(Math.round(width), 1, n);
  const anchors = [];
  let last = -1;
  for (let i = 0; i < A; i++) {
    const start = A > 1 ? Math.round((i * (n - width)) / (A - 1)) : Math.round((n - width) / 2);
    if (start === last) continue; // more anchors asked for than there are distinct stretches
    last = start;
    anchors.push({ i: anchors.length, start, width, c: start + (width - 1) / 2 });
  }
  const step = anchors.length > 1 ? (anchors[anchors.length - 1].c - anchors[0].c) / (anchors.length - 1) : n;
  return { count: n, width, step, anchors };
}

// The whole sequence for several runs, in the order the lights show it: a dark reference first and again every `darkEvery`
// anchors (exposure drifts), then each anchor. plans: [{key, first, plan}] with first = the run's first pixel on the light.
// Items: {kind: "dark"} or {kind: "seg", key, a, start (a pixel of the light), count, dark (index of its reference)}.
export function glowSchedule(plans, darkEvery = GLOW.darkEvery) {
  const items = [{ kind: "dark" }];
  let dark = 0, since = 0;
  for (const p of plans) {
    for (const a of p.plan.anchors) {
      if (since >= darkEvery) {
        items.push({ kind: "dark" });
        dark = items.length - 1;
        since = 0;
      }
      items.push({ kind: "seg", key: p.key, a: a.i, start: p.first + a.start, count: a.width, dark });
      since++;
    }
  }
  return items;
}

// camera pixels per small pixel for a frame w wide
export const glowFactor = (w) => Math.max(1, Math.round(w / GLOW.smallWidth));

// block-average luma down by f: {data: Float32Array, w, h, f}. Averaging also beats down sensor noise.
export function downscaleLuma(luma, w, h, f = glowFactor(w)) {
  const sw = Math.max(1, Math.floor(w / f)), sh = Math.max(1, Math.floor(h / f));
  const data = new Float32Array(sw * sh), inv = 1 / (f * f);
  for (let y = 0; y < sh; y++) {
    for (let x = 0; x < sw; x++) {
      let s = 0;
      for (let j = 0; j < f; j++) {
        const r = (y * f + j) * w + x * f;
        for (let i = 0; i < f; i++) s += luma[r + i];
      }
      data[y * sw + x] = s * inv;
    }
  }
  return { data, w: sw, h: sh, f };
}

function blur121(src, w, h) {
  const tmp = new Float32Array(w * h), out = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    const r = y * w;
    for (let x = 0; x < w; x++) tmp[r + x] = src[r + (x > 0 ? x - 1 : x)] + 2 * src[r + x] + src[r + (x < w - 1 ? x + 1 : x)];
  }
  for (let y = 0; y < h; y++) {
    const y0 = (y > 0 ? y - 1 : y) * w, y1 = y * w, y2 = (y < h - 1 ? y + 1 : y) * w;
    for (let x = 0; x < w; x++) out[y1 + x] = (tmp[y0 + x] + 2 * tmp[y1 + x] + tmp[y2 + x]) / 16;
  }
  return out;
}

function medianOf(arr, stride = 1) {
  const s = [];
  for (let i = 0; i < arr.length; i += stride) s.push(arr[i]);
  s.sort((a, b) => a - b);
  return s.length ? s[s.length >> 1] : 0;
}

// lit - dark, minus the frame-wide shift (auto exposure), then softened: the picture the search works on
function glowDiff(dark, lit, w, h) {
  const d = new Float32Array(w * h);
  for (let i = 0; i < d.length; i++) d[i] = lit[i] - dark[i];
  const off = medianOf(d, Math.max(1, d.length >> 14));
  for (let i = 0; i < d.length; i++) d[i] -= off; // signed: blurring keeps the noise symmetric, so its size can be measured
  return blur121(blur121(d, w, h), w, h);
}

// The glow in one anchor frame. dark and lit: small luma arrays (w x h). Returns
// {x, y, conf (0-1), peak, snr, area, dominance, reason}, x/y in full camera pixels (opts.f = camera pixels per small pixel);
// reason is null when found, else "none" | "weak" | "wide" (x and y are null). Only blobs at least minArea small pixels big (and minCore
// big at half their peak) count, so specks are ignored; the biggest by brightness-weighted mass wins. A sharper central cut gives the centre.
export function findGlow(dark, lit, w, h, opts = {}) {
  const f = opts.f ?? 1;
  const s = glowDiff(dark, lit, w, h);
  const med = medianOf(s, Math.max(1, s.length >> 14));
  const dev = new Float32Array(Math.ceil(s.length / Math.max(1, s.length >> 14)));
  for (let i = 0, j = 0; i < s.length; i += Math.max(1, s.length >> 14), j++) dev[j] = Math.abs(s[i] - med);
  const sigma = Math.max(0.25, medianOf(dev) * 1.4826);
  const t0 = Math.max(opts.floor ?? 1.2, 3.5 * sigma);
  const cap = 4 * t0;
  const minArea = opts.minArea ?? Math.max(6, Math.round(w * h * 0.00006));
  // connected regions above t0
  const lab = new Int32Array(s.length);
  const stack = new Int32Array(s.length);
  const regions = [];
  for (let p0 = 0; p0 < s.length; p0++) {
    if (s[p0] <= t0 || lab[p0]) continue;
    const id = regions.length + 1;
    let sp = 0, area = 0, mass = 0, peak = 0;
    stack[sp++] = p0;
    lab[p0] = id;
    while (sp) {
      const p = stack[--sp];
      area++;
      mass += Math.min(s[p], cap) - t0; // clipped: how far it spreads counts more than a flare's brightness
      if (s[p] > peak) peak = s[p];
      const x = p % w;
      if (x > 0 && s[p - 1] > t0 && !lab[p - 1]) (lab[p - 1] = id), (stack[sp++] = p - 1);
      if (x < w - 1 && s[p + 1] > t0 && !lab[p + 1]) (lab[p + 1] = id), (stack[sp++] = p + 1);
      if (p >= w && s[p - w] > t0 && !lab[p - w]) (lab[p - w] = id), (stack[sp++] = p - w);
      if (p + w < s.length && s[p + w] > t0 && !lab[p + w]) (lab[p + w] = id), (stack[sp++] = p + w);
    }
    regions.push({ id, area, mass, peak });
  }
  // a glint is small at half its peak, a puddle is not: count each region's pixels above half of its own peak
  for (let p = 0; p < s.length; p++) if (lab[p] && s[p] > 0.5 * regions[lab[p] - 1].peak) regions[lab[p] - 1].core = (regions[lab[p] - 1].core || 0) + 1;
  const minCore = opts.minCore ?? 16;
  const real = regions.filter((r) => r.area >= minArea && (r.core || 0) >= minCore && r.peak >= 6 * sigma); // noise ripples don't reach 6 sigma
  const none = (reason, extra = {}) => ({ x: null, y: null, conf: 0, peak: 0, snr: 0, area: 0, dominance: 0, reason, ...extra });
  if (!real.length) return none("none");
  const best = real.reduce((a, b) => (b.mass > a.mass ? b : a));
  const total = real.reduce((a, r) => a + r.mass, 0);
  const snr = best.peak / sigma, dominance = best.mass / total;
  const info = { peak: best.peak, snr, area: best.area, dominance };
  if (best.peak < (opts.minPeak ?? 3) || snr < 6) return none("weak", info);
  if (best.area > (opts.maxAreaFrac ?? 0.25) * w * h) return none("wide", info);
  const cut = Math.max(t0, 0.25 * best.peak);
  let sx = 0, sy = 0, sw = 0;
  for (let p = 0; p < s.length; p++) {
    if (lab[p] !== best.id || s[p] <= cut) continue;
    const wt = s[p] - cut;
    sx += wt * (p % w);
    sy += wt * ((p / w) | 0);
    sw += wt;
  }
  const conf = clamp((snr - 4) / 10, 0, 1) * clamp((dominance - 0.4) / 0.4, 0, 1);
  return { x: (sx / sw + 0.5) * f - 0.5, y: (sy / sw + 0.5) * f - 0.5, conf, reason: null, ...info };
}

// Where the person tapped (full camera pixels): nudged to the centre of any glow within `radius` small pixels, else exactly there.
export function refineTap(dark, lit, w, h, tapX, tapY, opts = {}) {
  const f = opts.f ?? 1, tx = (tapX + 0.5) / f - 0.5, ty = (tapY + 0.5) / f - 0.5;
  const R = opts.radius ?? Math.max(6, Math.round(w * 0.06));
  const s = glowDiff(dark, lit, w, h);
  let pk = 0;
  const x0 = Math.max(0, Math.floor(tx - R)), x1 = Math.min(w - 1, Math.ceil(tx + R)), y0 = Math.max(0, Math.floor(ty - R)), y1 = Math.min(h - 1, Math.ceil(ty + R));
  for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) if ((x - tx) ** 2 + (y - ty) ** 2 <= R * R) pk = Math.max(pk, s[y * w + x]);
  if (pk < (opts.minPeak ?? 3)) return { x: tapX, y: tapY, snapped: false };
  const cut = 0.3 * pk;
  let sx = 0, sy = 0, sw = 0;
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      if ((x - tx) ** 2 + (y - ty) ** 2 > R * R || s[y * w + x] <= cut) continue;
      const wt = s[y * w + x] - cut;
      sx += wt * x;
      sy += wt * y;
      sw += wt;
    }
  }
  return { x: (sx / sw + 0.5) * f - 0.5, y: (sy / sw + 0.5) * f - 0.5, snapped: true };
}

// grey RGBA of the glow (lit - dark, stretched and brightened) for showing the frozen frame to the person
export function glowPreview(dark, lit, w, h) {
  const d = new Float32Array(w * h);
  for (let i = 0; i < d.length; i++) d[i] = Math.max(0, lit[i] - dark[i]);
  const sorted = Float32Array.from(d).sort();
  const top = Math.max(6, sorted[Math.floor(sorted.length * 0.998)]);
  const out = new Uint8ClampedArray(w * h * 4);
  for (let i = 0, j = 0; i < d.length; i++, j += 4) {
    const v = Math.round(255 * Math.sqrt(clamp(d[i] / top, 0, 1)));
    out[j] = out[j + 1] = out[j + 2] = v;
    out[j + 3] = 255;
  }
  return out;
}

// monotone cubic (Fritsch-Carlson) through (xs, ys), straight lines beyond the ends: passes every anchor, never overshoots
function pchip(xs, ys) {
  const m = xs.length, d = new Float64Array(m), h = new Float64Array(m - 1), del = new Float64Array(m - 1);
  for (let k = 0; k < m - 1; k++) {
    h[k] = xs[k + 1] - xs[k];
    del[k] = (ys[k + 1] - ys[k]) / h[k];
  }
  d[0] = del[0];
  d[m - 1] = del[m - 2];
  for (let k = 1; k < m - 1; k++) {
    if (del[k - 1] * del[k] <= 0) d[k] = 0;
    else {
      const w1 = 2 * h[k] + h[k - 1], w2 = h[k] + 2 * h[k - 1];
      d[k] = (w1 + w2) / (w1 / del[k - 1] + w2 / del[k]);
    }
  }
  return (t) => {
    if (t <= xs[0]) return ys[0] + d[0] * (t - xs[0]);
    if (t >= xs[m - 1]) return ys[m - 1] + d[m - 1] * (t - xs[m - 1]);
    let lo = 0, hi = m - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (xs[mid] <= t) lo = mid;
      else hi = mid;
    }
    const u = (t - xs[lo]) / h[lo], u2 = u * u, u3 = u2 * u;
    return (2 * u3 - 3 * u2 + 1) * ys[lo] + (u3 - 2 * u2 + u) * h[lo] * d[lo] + (-2 * u3 + 3 * u2) * ys[lo + 1] + (u3 - u2) * h[lo] * d[lo + 1];
  };
}

// Pixel places for a run from its anchors. anchors: [{c (centre pixel), x, y, conf, manual?}] in string order; x null/undefined
// = not found. Status: "ok" (used), "low" (found but conf < minConf and not confirmed by the person), "missing", "outlier"
// (far off the line its neighbours make: a wrong blob). Only "ok" anchors are used; between them the path is a smooth monotone
// cubic through the anchors in string order, straight beyond the ends. est[i] = 1 for pixels farther than 0.6 of an anchor
// step from every ok anchor (a gap the person skipped). Returns {x, y, est, found, anchors, step} or null with fewer than 2 ok.
export function solveGlow(count, anchors, opts = {}) {
  const minConf = opts.minConf ?? GLOW.minConf;
  const out = anchors.map((a) => {
    const has = a.x != null && a.y != null && Number.isFinite(a.x) && Number.isFinite(a.y);
    const status = !has ? "missing" : a.manual || a.conf >= minConf ? "ok" : "low";
    return { ...a, status };
  });
  // wrong blobs: compare each ok anchor with the straight line its ok neighbours make
  const okIdx = () => out.map((a, k) => (a.status === "ok" ? k : -1)).filter((k) => k >= 0);
  for (let round = 0; round < 4; round++) {
    const ids = okIdx();
    if (ids.length < 4) break;
    const gaps = [];
    for (let j = 1; j < ids.length; j++) gaps.push(Math.hypot(out[ids[j]].x - out[ids[j - 1]].x, out[ids[j]].y - out[ids[j - 1]].y) / (out[ids[j]].c - out[ids[j - 1]].c));
    const unit = medianOf(gaps); // camera pixels per string pixel
    let worst = -1, worstDev = 0;
    for (let j = 0; j < ids.length; j++) {
      const a = out[ids[j]];
      let ex, ey;
      if (j > 0 && j < ids.length - 1) {
        const p = out[ids[j - 1]], q = out[ids[j + 1]], t = (a.c - p.c) / (q.c - p.c);
        ex = p.x + (q.x - p.x) * t;
        ey = p.y + (q.y - p.y) * t;
      } else {
        const n1 = out[ids[j === 0 ? 1 : j - 1]], n2 = out[ids[j === 0 ? 2 : j - 2]];
        const t = (a.c - n1.c) / (n2.c - n1.c);
        ex = n1.x + (n2.x - n1.x) * t;
        ey = n1.y + (n2.y - n1.y) * t;
      }
      const reach = Math.max(Math.abs(a.c - out[ids[Math.max(0, j - 1)]].c), Math.abs(out[ids[Math.min(ids.length - 1, j + 1)]].c - a.c));
      const dev = Math.hypot(a.x - ex, a.y - ey) / Math.max(unit * reach, 1e-6);
      if (dev > worstDev && !a.manual) (worstDev = dev), (worst = ids[j]); // a person's tap is never second-guessed
    }
    if (worst < 0 || worstDev < (opts.outlier ?? 1.25)) break;
    out[worst].status = "outlier";
  }
  const ids = okIdx();
  const n = Math.max(1, Math.floor(count));
  if (ids.length < 2) return { x: new Float64Array(n), y: new Float64Array(n), est: new Uint8Array(n).fill(1), found: ids.length, anchors: out, step: n, ok: false };
  const xs = ids.map((k) => out[k].c), fx = pchip(xs, ids.map((k) => out[k].x)), fy = pchip(xs, ids.map((k) => out[k].y));
  const x = new Float64Array(n), y = new Float64Array(n), est = new Uint8Array(n);
  const spacing = [];
  for (let j = 1; j < xs.length; j++) spacing.push(xs[j] - xs[j - 1]);
  const step = medianOf(anchors.length > 1 ? anchors.slice(1).map((a, k) => a.c - anchors[k].c) : spacing) || n;
  let j = 0;
  for (let i = 0; i < n; i++) {
    x[i] = fx(i);
    y[i] = fy(i);
    while (j + 1 < xs.length && Math.abs(xs[j + 1] - i) <= Math.abs(xs[j] - i)) j++;
    if (Math.abs(xs[j] - i) > 0.6 * step) est[i] = 1;
  }
  return { x, y, est, found: ids.length, anchors: out, step, ok: true };
}
