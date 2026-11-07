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
