// Light Show Studio: camera-scan maths. Pure functions, no DOM, so they run in the browser and in node (tests/js/test_scan.mjs).
// Spec: docs/MAPPING_V2.md section 3. Two code modes (the panel asks the light for one in map/scan/start):
//  - COLOUR (default): frame 0 = dark, frames 1-3 = every pixel red, green, blue (calibration), then one frame per base-3 digit of
//    the pixel's code (index + 1) with each pixel red/green/blue for its digit, then a check-digit frame. See colour* below.
//  - WHITE (fallback): frame k of a scan: 0 = all off (dark reference), 1 = all on (dim white), then for each
//    bit b of the pixel index two frames: 2+2b lights the pixels whose bit b is set, 3+2b lights the others (the inverse).

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

// The frame-wide level shift of `frame` against `dark` (auto exposure, ambient light drifting between frames), for channel c of nc
// interleaved channels: the 40th percentile of the difference on a sparse sample, within +-12 (a lit scene does not count as drift;
// lit pixels are a minority, and a stray lamp turning on cannot move it by more than the cap).
function frameShift(frame, dark, c = 0, nc = 1) {
  const n = frame.length / nc, stride = Math.max(1, n >> 14), v = [];
  for (let i = 0; i < n; i += stride) v.push(frame[i * nc + c] - dark[i * nc + c]);
  v.sort((a, b) => a - b);
  return clamp(v[Math.floor(v.length * 0.4)] ?? 0, -12, 12);
}

// bright local maxima of (allOn - dark): [{x, y, px, py, v}] strongest first, at least minDist apart
export function findBlobs(dark, allOn, w, h, opts = {}) {
  const diff = new Uint8ClampedArray(w * h);
  for (let i = 0; i < diff.length; i++) diff[i] = Math.max(0, allOn[i] - dark[i]);
  return findBlobsInDiff(diff, w, h, opts);
}

// the same from a ready-made difference picture (Uint8)
export function findBlobsInDiff(diff, w, h, opts = {}) {
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
  const shift = frames.map((f, k) => (k === 0 ? 0 : frameShift(f, dark)));
  const blobs = findBlobs(dark, frames[1], w, h, opts);
  const val = (k, b) => Math.max(0, frames[k][b.py * w + b.px] - dark[b.py * w + b.px] - shift[k]);
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

// ======================================================================================================================
// Colour scan. Mirrors scan_codes.py. Frames are RGB, 3 bytes per pixel (rgbFromRGBA). Frame 0 dark; 1, 2, 3 = all red, green,
// blue; 4..4+D-1 = base-3 digit j of (index + 1), a pixel showing primary (digit + j) % 3; 4+D = check digit (sum of digits) % 3,
// shown the same way with j = D. The decoder learns each LED's own red, green and blue from frames 1-3 (as the camera sees them:
// white balance, LED tint, diffusers all included) and reads every later frame as "nearest of those three by chromaticity".
// ======================================================================================================================
export const COLOUR_LEVEL = 140;
export const COLOUR_CAL = 4; // dark + red + green + blue
export const MODES = ["colour", "white"];

// digits that hold the codes 1..n
export function digitCount(n) {
  let d = 1;
  while (3 ** d <= n) d++;
  return d;
}
export const colourFrames = (n) => COLOUR_CAL + digitCount(n) + 1;

// primary shown by pixel i in colour frame k: null = dark, 0 red, 1 green, 2 blue
export function colourClass(i, k, n) {
  if (k === 0) return null;
  if (k < COLOUR_CAL) return k - 1;
  const j = k - COLOUR_CAL, D = digitCount(n);
  let code = i + 1, sum = 0, d = 0;
  for (let q = 0; q < D; q++) {
    const dq = code % 3;
    code = (code - dq) / 3;
    sum += dq;
    if (q === j) d = dq;
  }
  if (j === D) d = sum % 3;
  return (d + j) % 3;
}

export const primaryColour = (c, level = COLOUR_LEVEL) => [c === 0 ? level : 0, c === 1 ? level : 0, c === 2 ? level : 0];

// frame count of a mode ("colour" | "white")
export const framesFor = (n, mode = "colour") => (mode === "white" ? scanFrames(n) : colourFrames(n));

// RGBA bytes -> RGB bytes (3 per pixel)
export function rgbFromRGBA(rgba, w, h) {
  const out = new Uint8ClampedArray(w * h * 3);
  for (let i = 0, j = 0, k = 0; i < w * h; i++, j += 4, k += 3) {
    out[k] = rgba[j];
    out[k + 1] = rgba[j + 1];
    out[k + 2] = rgba[j + 2];
  }
  return out;
}

// RGB bytes -> luma bytes (for the sync blinks of the video fallback)
export function lumaFromRGB(rgb, w, h) {
  const out = new Uint8ClampedArray(w * h);
  for (let i = 0, j = 0; i < out.length; i++, j += 3) out[i] = (rgb[j] * 77 + rgb[j + 1] * 150 + rgb[j + 2] * 29) >> 8;
  return out;
}

// the reading at a blob's brightest pixel (the sub-pixel centre is not needed here), minus the same in dark: [r, g, b], not below 0.
// One pixel, not a 3x3 mean: strips with LEDs 3 px apart would otherwise mix their neighbours' colours into the reading.
function sampleRGB(frame, dark, w, px, py, sh = [0, 0, 0]) {
  const o = (py * w + px) * 3;
  return [Math.max(0, frame[o] - dark[o] - sh[0]), Math.max(0, frame[o + 1] - dark[o + 1] - sh[1]), Math.max(0, frame[o + 2] - dark[o + 2] - sh[2])];
}

const l1 = (v) => v[0] + v[1] + v[2];
const dist3 = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

// Which of the three calibrated colours a reading is: {cls, margin (0-1, how clearly), level (reading / calibrated brightness)}.
// cal = [{c: chromaticity (sums to 1), m: brightness}] for red, green, blue. margin is (d2 - d1) / (d2 + d1) over the two nearest.
export function classifyColour(u, cal) {
  const m = l1(u);
  if (m <= 0) return { cls: -1, margin: 0, level: 0 };
  const cu = [u[0] / m, u[1] / m, u[2] / m];
  const d = cal.map((k) => dist3(cu, k.c));
  let best = 0;
  for (let k = 1; k < 3; k++) if (d[k] < d[best]) best = k;
  const others = d.filter((_, k) => k !== best);
  const d2 = Math.min(...others);
  return { cls: best, margin: (d2 - d[best]) / (d2 + d[best] + 1e-9), level: m / cal[best].m };
}

// Decode a colour scan. frames: RGB arrays (3 bytes per pixel), colourFrames(n) of them, all w x h. Returns what decodeScan does:
// {points, found, blobs, rejected, w, h}, points[i] = [x, y, confidence] or null. A bright spot is a pixel only if
//  - its red, green and blue calibration responses are bright and clearly different colours (a lamp or reflection that is white
//    in all three is not an LED: badCal),
//  - in every digit frame it reads clearly as one of those three colours and not dimmer than 30% of it (badDigit),
//  - the check digit agrees (badCheck), and the code is a real pixel (outOfRange), and nobody else read it better (duplicate).
export function decodeColourScan(frames, n, w, h, opts = {}) {
  const D = digitCount(n), F = COLOUR_CAL + D + 1;
  if (frames.length < F) throw new Error(`Expected ${F} frames, got ${frames.length}.`);
  const dark = frames[0];
  // where the lights are: the three calibration frames' brightening, added up
  const diff = new Uint8ClampedArray(w * h);
  for (let p = 0, o = 0; p < diff.length; p++, o += 3) {
    let sum = 0;
    for (let k = 1; k <= 3; k++) {
      const f = frames[k];
      for (let c = 0; c < 3; c++) sum += Math.max(0, f[o + c] - dark[o + c]);
    }
    diff[p] = sum / 6; // about the level a white LED would have in luma
  }
  const blobs = findBlobsInDiff(diff, w, h, opts);
  // per frame and channel: the level shift of auto exposure / ambient light against the dark frame
  const shift = frames.map((f, k) => (k === 0 ? [0, 0, 0] : [0, 1, 2].map((c) => frameShift(f, dark, c, 3))));
  const minSep = opts.minSeparation ?? 0.2, minMargin = opts.minMargin ?? 0.35, minLevel = opts.minLevel ?? 0.3;
  const minCal = opts.minCal ?? 12;
  const rej = { badCal: 0, badDigit: 0, badCheck: 0, outOfRange: 0, duplicate: 0 };
  const points = new Array(n).fill(null);
  for (const bl of blobs) {
    const cal = [];
    let bad = false;
    for (let k = 0; k < 3; k++) {
      const v = sampleRGB(frames[1 + k], dark, w, bl.px, bl.py, shift[1 + k]), m = l1(v);
      if (m < minCal) {
        bad = true;
        break;
      }
      cal.push({ c: [v[0] / m, v[1] / m, v[2] / m], m });
    }
    if (bad || dist3(cal[0].c, cal[1].c) < minSep || dist3(cal[0].c, cal[2].c) < minSep || dist3(cal[1].c, cal[2].c) < minSep) {
      rej.badCal++;
      continue;
    }
    const digits = [];
    let conf = 1;
    for (let j = 0; j <= D && !bad; j++) {
      const r = classifyColour(sampleRGB(frames[COLOUR_CAL + j], dark, w, bl.px, bl.py, shift[COLOUR_CAL + j]), cal);
      if (r.cls < 0 || r.margin < minMargin || r.level < minLevel) {
        bad = true;
        break;
      }
      conf = Math.min(conf, r.margin);
      digits.push((((r.cls - j) % 3) + 3) % 3);
    }
    if (bad) {
      rej.badDigit++;
      continue;
    }
    const check = digits.pop();
    if (digits.reduce((a, d) => a + d, 0) % 3 !== check) {
      rej.badCheck++;
      continue;
    }
    let code = 0;
    for (let q = D - 1; q >= 0; q--) code = code * 3 + digits[q];
    const idx = code - 1;
    if (idx < 0 || idx >= n) {
      rej.outOfRange++;
      continue;
    }
    const old = points[idx];
    if (old && old[2] >= conf) {
      rej.duplicate++;
      continue;
    }
    if (old) rej.duplicate++;
    points[idx] = [bl.x, bl.y, conf];
  }
  return { points, found: points.filter(Boolean).length, blobs: blobs.length, rejected: rej, w, h, mode: "colour" };
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
export const GLOW = { minAnchors: 16, maxAnchors: 24, minWidth: 3, maxWidth: 6, darkEvery: 12, minConf: 0.35, smallWidth: 480, minPeak: 2 };

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

// Which stretches to light for a run of `count` pixels: [{i, start, width, c}], start relative to the run, c = the stretch's
// centre pixel (a fraction when the width is even). Default ~16-24 anchors, 3-6 pixels wide (small flashes: the glow of a few
// pixels is a tight patch whose centre is well defined, and it spills less on neighbours; findGlow's `hint` says when a spot needs
// more, see glowAdaptWidth); opts {anchors, width}.
export function glowPlan(count, opts = {}) {
  const n = Math.max(1, Math.floor(count));
  let A = opts.anchors ?? Math.min(GLOW.maxAnchors, Math.max(GLOW.minAnchors, Math.round(1.2 * Math.sqrt(n))));
  A = clamp(Math.round(A), 1, n);
  let width = opts.width ?? clamp(Math.round((0.35 * n) / A), GLOW.minWidth, GLOW.maxWidth);
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

// Robust background for lit - dark. Exposure and white balance drift scale the whole picture a little (lit = a + b * dark outside
// the glow), so a line is fitted through the per-brightness-bin medians of (dark, lit): the glow covers a minority of every bin, so
// medians ignore it. With a flat dark picture only the offset is taken. Returns {a, b}.
function glowBackground(dark, lit) {
  const n = dark.length, stride = Math.max(1, n >> 14), idx = [];
  for (let i = 0; i < n; i += stride) idx.push(i);
  idx.sort((p, q) => dark[p] - dark[q]);
  const bins = 8, per = Math.floor(idx.length / bins), bx = [], by = [];
  if (per >= 8) {
    for (let k = 0; k < bins; k++) {
      const part = idx.slice(k * per, (k + 1) * per);
      const dk = part.map((i) => dark[i]).sort((p, q) => p - q), df = part.map((i) => lit[i] - dark[i]).sort((p, q) => p - q);
      bx.push(dk[dk.length >> 1]);
      by.push(df[df.length >> 1]);
    }
  }
  const off = by.length ? medianOf(by) : medianOf(Float32Array.from(idx, (i) => lit[i] - dark[i]));
  if (bx.length < 4 || bx[bx.length - 1] - bx[0] < 8) return { a: off, b: 0 };
  let mx = 0, my = 0;
  for (let k = 0; k < bx.length; k++) (mx += bx[k]), (my += by[k]);
  mx /= bx.length;
  my /= by.length;
  let sxx = 0, sxy = 0;
  for (let k = 0; k < bx.length; k++) (sxx += (bx[k] - mx) ** 2), (sxy += (bx[k] - mx) * (by[k] - my));
  const b = clamp(sxy / (sxx || 1), -0.4, 0.6);
  return { a: my - b * mx, b }; // lit - dark = a + b * dark outside the glow
}

// subtract a plane fitted (trimmed against the glow) to d: slow gradients left by gain drift or a lamp changing do not make blobs
function removePlane(d, w, h) {
  const stride = Math.max(1, Math.floor(d.length / 6000)), pts = [];
  for (let i = 0; i < d.length; i += stride) pts.push(i);
  const ab = pts.map((i) => Math.abs(d[i]));
  const mad = Math.max(0.2, medianOf(ab) * 1.4826);
  let keep = pts, c = [0, 0, 0];
  for (let it = 0; it < 4; it++) {
    // least squares of d = c0 + c1 * u + c2 * v, u and v in -0.5..0.5
    const A = [0, 0, 0, 0, 0, 0, 0, 0, 0], B = [0, 0, 0];
    for (const i of keep) {
      const u = (i % w) / w - 0.5, v = ((i / w) | 0) / h - 0.5, r = [1, u, v];
      for (let p = 0; p < 3; p++) {
        B[p] += r[p] * d[i];
        for (let q = 0; q < 3; q++) A[p * 3 + q] += r[p] * r[q];
      }
    }
    const iv = inv3(A);
    if (!iv) return;
    c = [0, 1, 2].map((p) => iv.inv[p * 3] * B[0] + iv.inv[p * 3 + 1] * B[1] + iv.inv[p * 3 + 2] * B[2]);
    keep = pts.filter((i) => {
      const u = (i % w) / w - 0.5, v = ((i / w) | 0) / h - 0.5;
      return Math.abs(d[i] - (c[0] + c[1] * u + c[2] * v)) < 3 * mad;
    });
    if (keep.length < 100) return;
  }
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) d[y * w + x] -= c[0] + c[1] * (x / w - 0.5) + c[2] * (y / h - 0.5);
}

// lit - dark with the background (auto exposure, white balance, slow gradients) taken out, then softened: the picture the search
// works on. opts {darkAfter: a second dark picture taken after the lit one (averaged with dark: drift in between cancels), passes}.
// Returns {s, sat (fraction of lit pixels at the sensor's top), gain (the fitted b)}.
function glowDiffEx(dark, lit, w, h, opts = {}) {
  let dk = dark;
  if (opts.darkAfter && opts.darkAfter.length === dark.length) {
    dk = new Float32Array(dark.length);
    for (let i = 0; i < dk.length; i++) dk[i] = 0.5 * (dark[i] + opts.darkAfter[i]);
  }
  const bg = glowBackground(dk, lit);
  const d = new Float32Array(w * h);
  let sat = 0;
  for (let i = 0; i < d.length; i++) {
    d[i] = lit[i] - dk[i] - bg.a - bg.b * dk[i]; // signed: blurring keeps the noise symmetric, so its size can be measured
    if (lit[i] >= (opts.satLevel ?? 250)) sat++;
  }
  removePlane(d, w, h);
  let s = blur121(blur121(d, w, h), w, h);
  for (let k = 2; k < (opts.passes ?? 2); k += 2) s = blur121(blur121(s, w, h), w, h);
  return { s, sat: sat / d.length, gain: 1 + bg.b };
}
// How much of white noise survives (s - blur(s)) when s is white noise softened by `passes` double-121 blurs: the ratio of the
// two kernels' energies, so measuring the high-pass noise gives the noise of s itself.
const _nr = {};
function noiseRatio(passes) {
  if (_nr[passes]) return _nr[passes];
  let K = [1];
  const conv = (a, b) => {
    const o = new Array(a.length + b.length - 1).fill(0);
    a.forEach((x, i) => b.forEach((y, j) => (o[i + j] += x * y)));
    return o;
  };
  for (let q = 0; q < passes; q++) K = conv(K, [0.25, 0.5, 0.25]);
  const AB = conv(K, [0.25, 0.5, 0.25]);
  const pad = (AB.length - K.length) / 2, Kp = AB.map((_, i) => K[i - pad] || 0);
  const e = (a) => a.reduce((s, v) => s + v * v, 0), dot = (a, b) => a.reduce((s, v, i) => s + v * b[i], 0);
  const kk = e(Kp) ** 2, hh = kk - 2 * dot(Kp, AB) ** 2 + e(AB) ** 2;
  return (_nr[passes] = Math.sqrt(Math.max(1e-6, hh / kk)));
}
function glowDiff(dark, lit, w, h, opts = {}) {
  return glowDiffEx(dark, lit, w, h, opts).s;
}

// ---- the glow in one anchor frame -----------------------------------------------------------------------------------------
// dark and lit: small luma arrays (w x h). Returns
//   {x, y, conf (0-1), peak, snr, area, dominance, reason, size, elong, angle, saturated, clipped, hint, alt, ...}
// x/y in full camera pixels (opts.f = camera pixels per small pixel); reason is null when found, else "none" | "weak" | "wide"
// (x and y are null). Only blobs at least minArea small pixels big (and minCore big at half their peak) count, so specks are
// ignored. Patches that merge into one connected region but have separate peaks (valley between them) are told apart, and the
// best of all candidates wins by brightness-weighted mass (and the prior, below). The centre is the centroid of the brightest part
// (above a fraction of the peak that grows with the signal-to-noise: a clean glow ignores its faint skirt, a dim one keeps more
// pixels to average the noise).
// Options:
//   expect: {x, y, r}   where the glow should be (full camera pixels), e.g. glowPredict(). A soft prior: candidates farther than
//                       r from it lose weight; one far outside is trusted less. Resolves "two patches" and reflections.
//   gate:   {x, y, r}   hard search window (full camera pixels): nothing outside counts.
//   darkAfter           a second dark picture (taken after the lit one) averaged with dark.
//   dim                 true: look harder (more smoothing, lower floor); findGlow also does this by itself when nothing was found.
//   floor, minPeak, minArea, minCore, maxAreaFrac, satLevel
// Extra result fields: size (equivalent diameter at half peak, camera px), elong (major/minor axis, >= 1), angle (major axis, radians,
// y down), spread {sx, sy} (std dev, camera px), saturated (fraction of lit pixels at the top), clipped (touches the picture edge),
// alt: other candidates [{x, y, share, peak}] strongest first (share = their weight relative to the winner) so a later step can
// choose the one that fits the string (glowReconsider), hint: null | "wider" | "narrower" (what the next try should change),
// off (distance from expect in units of r), gain (fitted exposure change).
export function findGlow(dark, lit, w, h, opts = {}) {
  const r1 = findGlowPass(dark, lit, w, h, opts, opts.dim ? 12 : 2);
  if (!opts.dim && (r1.reason === "none" || r1.reason === "weak") && !opts.noRetry) {
    const r2 = findGlowPass(dark, lit, w, h, { ...opts, minPeak: (opts.minPeak ?? GLOW.minPeak) * 0.6 }, 12);
    if (!r2.reason) return { ...r2, hint: r2.hint || "wider" };
    if (r1.reason === "none" && r2.reason === "weak") return r2;
  }
  return r1;
}

function findGlowPass(dark, lit, w, h, opts, passes) {
  const f = opts.f ?? 1;
  const { s, sat, gain } = glowDiffEx(dark, lit, w, h, { ...opts, passes });
  const stride = Math.max(1, s.length >> 14);
  const med = medianOf(s, stride);
  const dev = new Float32Array(Math.ceil(s.length / stride));
  for (let i = 0, j = 0; i < s.length; i += stride, j++) dev[j] = Math.abs(s[i] - med);
  const sigmaAll = medianOf(dev) * 1.4826; // inflated when the glow itself fills much of the picture
  // noise from the picture's fine structure only: what is left of s after taking away its own softening, divided by how much of white
  // noise that leaves. Big soft patches and gradients do not count, so a dim wide glow is not hidden by its own size.
  const s2 = blur121(s, w, h), hp = new Float32Array(dev.length);
  for (let i = 0, j = 0; i < s.length; i += stride, j++) hp[j] = s[i] - s2[i];
  const hpMed = medianOf(hp);
  for (let j = 0; j < hp.length; j++) hp[j] = Math.abs(hp[j] - hpMed);
  const sigmaHF = (medianOf(hp) * 1.4826) / noiseRatio(passes);
  const sigma = Math.max(0.15, sigmaHF, 0.3 * sigmaAll);
  const t0 = Math.max(opts.floor ?? (passes > 2 ? 0.6 : 1.0), 3.5 * sigma);
  const cap = 4 * t0;
  const minArea = opts.minArea ?? Math.max(6, Math.round(w * h * 0.00006));
  const minCore = opts.minCore ?? (passes > 2 ? 60 : 16); // the heavy smoothing of the dim search spreads a speck over ~25 pixels
  const none = (reason, extra = {}) => ({ x: null, y: null, conf: 0, peak: 0, snr: 0, area: 0, dominance: 0, reason, saturated: sat, gain, hint: reason === "weak" || reason === "none" ? "wider" : reason === "wide" ? "narrower" : null, alt: [], ...extra });
  // the search window
  const gt = opts.gate;
  if (gt) {
    const gx = (gt.x + 0.5) / f - 0.5, gy = (gt.y + 0.5) / f - 0.5, gr = (gt.r ?? 0.2 * w * f) / f;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if ((x - gx) ** 2 + (y - gy) ** 2 > gr * gr) s[y * w + x] = 0;
  }
  // connected regions above t0
  const lab = new Int32Array(s.length), stack = new Int32Array(s.length);
  const regions = [];
  for (let p0 = 0; p0 < s.length; p0++) {
    if (s[p0] <= t0 || lab[p0]) continue;
    const id = regions.length + 1;
    let sp = 0, area = 0, peak = 0;
    stack[sp++] = p0;
    lab[p0] = id;
    while (sp) {
      const p = stack[--sp];
      area++;
      if (s[p] > peak) peak = s[p];
      const x = p % w;
      if (x > 0 && s[p - 1] > t0 && !lab[p - 1]) (lab[p - 1] = id), (stack[sp++] = p - 1);
      if (x < w - 1 && s[p + 1] > t0 && !lab[p + 1]) (lab[p + 1] = id), (stack[sp++] = p + 1);
      if (p >= w && s[p - w] > t0 && !lab[p - w]) (lab[p - w] = id), (stack[sp++] = p - w);
      if (p + w < s.length && s[p + w] > t0 && !lab[p + w]) (lab[p + w] = id), (stack[sp++] = p + w);
    }
    regions.push({ id, area, peak });
  }
  if (!regions.length) return none("none");
  // split regions that hold several peaks with a real valley between them: a second smoothing finds the peaks
  const group = new Int32Array(s.length); // candidate id (1-based) of each pixel
  const cands = [];
  for (const r of regions) {
    if (r.area < minArea || r.peak < 4 * sigma) continue; // can't be a candidate; saves the peak search
    const pk = [];
    for (let p = 0; p < s.length; p++) {
      if (lab[p] !== r.id || s2[p] < 0.35 * r.peak || s2[p] <= t0 || s2[p] < 6 * sigma) continue;
      const x = p % w, y = (p / w) | 0;
      let isMax = true;
      for (let dy = -1; dy <= 1 && isMax; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          if (!dx && !dy) continue;
          const xx = x + dx, yy = y + dy;
          if (xx < 0 || yy < 0 || xx >= w || yy >= h) continue;
          const q = yy * w + xx, later = dy > 0 || (dy === 0 && dx > 0);
          if (later ? s2[q] > s2[p] : s2[q] >= s2[p]) {
            isMax = false;
            break;
          }
        }
      }
      if (isMax) pk.push(p);
    }
    pk.sort((a, b) => s2[b] - s2[a]);
    const kept = [];
    for (const p of pk) {
      const x = p % w, y = (p / w) | 0;
      let distinct = true;
      for (const q of kept) {
        const qx = q % w, qy = (q / w) | 0, dist = Math.hypot(qx - x, qy - y);
        if (dist < 3) {
          distinct = false;
          break;
        }
        let lo = Infinity;
        const steps = Math.ceil(dist);
        for (let k = 1; k < steps; k++) lo = Math.min(lo, s2[Math.round(y + ((qy - y) * k) / steps) * w + Math.round(x + ((qx - x) * k) / steps)]);
        if (lo >= 0.8 * s2[p] || s2[p] - lo < 5 * sigma) {
          distinct = false;
          break;
        }
      }
      if (distinct) kept.push(p);
      if (kept.length >= 6) break;
    }
    if (!kept.length) kept.push(pk[0] ?? 0);
    const base = cands.length;
    for (const p of kept) cands.push({ region: r.id, px: p % w, py: (p / w) | 0, area: 0, mass: 0, peak: 0, core: 0, x0: w, x1: 0, y0: h, y1: 0 });
    // each pixel to its nearest peak
    for (let p = 0; p < s.length; p++) {
      if (lab[p] !== r.id) continue;
      const x = p % w, y = (p / w) | 0;
      let bi = base;
      if (kept.length > 1) {
        let bd = Infinity;
        for (let k = 0; k < kept.length; k++) {
          const d = (cands[base + k].px - x) ** 2 + (cands[base + k].py - y) ** 2;
          if (d < bd) (bd = d), (bi = base + k);
        }
      }
      group[p] = bi + 1;
      const c = cands[bi];
      c.area++;
      c.mass += Math.min(s[p], cap) - t0; // clipped: how far it spreads counts more than a flare's brightness
      if (s[p] > c.peak) c.peak = s[p];
      if (x < c.x0) c.x0 = x;
      if (x > c.x1) c.x1 = x;
      if (y < c.y0) c.y0 = y;
      if (y > c.y1) c.y1 = y;
    }
  }
  for (let p = 0; p < s.length; p++) if (group[p] && s[p] > 0.5 * cands[group[p] - 1].peak) cands[group[p] - 1].core++;
  const real = cands.filter((c) => c.area >= minArea && c.core >= minCore && c.peak >= 6 * sigma); // noise ripples don't reach 6 sigma
  if (!real.length) return none("none");
  // the prior: where the glow should be
  const ex = opts.expect;
  const exx = ex ? (ex.x + 0.5) / f - 0.5 : 0, exy = ex ? (ex.y + 0.5) / f - 0.5 : 0, exr = ex ? Math.max(2, (ex.r ?? 0.15 * w * f) / f) : 1;
  for (const c of real) {
    c.off = ex ? Math.hypot(c.px - exx, c.py - exy) / exr : 0;
    c.score = c.mass * (ex ? Math.max(0.04, Math.exp(-0.5 * c.off * c.off)) : 1);
  }
  const best = real.reduce((a, b) => (b.score > a.score ? b : a));
  const total = real.reduce((a, c) => a + c.score, 0);
  const snr = best.peak / sigma, dominance = best.score / total;
  const info = { peak: best.peak, snr, area: best.area, dominance };
  const minPeak = opts.minPeak ?? GLOW.minPeak;
  if (best.peak < minPeak || snr < 6) return none("weak", info);
  if (best.area > (opts.maxAreaFrac ?? 0.25) * w * h) return none("wide", info);
  // centre: the brightest part of the winner
  const frac = clamp(0.5 - (0.2 * (30 - snr)) / 22, 0.3, 0.5); // snr >= 30: half of the peak, snr 8: 0.3
  const cut = Math.max(t0, frac * best.peak), gid = cands.indexOf(best) + 1;
  let sx = 0, sy = 0, sw = 0, sxx = 0, syy = 0, sxy = 0, half = 0;
  for (let p = 0; p < s.length; p++) {
    if (group[p] !== gid || s[p] <= cut) continue;
    const wt = s[p] - cut, x = p % w, y = (p / w) | 0;
    sx += wt * x;
    sy += wt * y;
    sw += wt;
    sxx += wt * x * x;
    syy += wt * y * y;
    sxy += wt * x * y;
    if (s[p] > 0.5 * best.peak) half++;
  }
  if (!sw) return none("weak", info);
  const mx = sx / sw, my = sy / sw;
  const vxx = Math.max(0, sxx / sw - mx * mx) + 1 / 12, vyy = Math.max(0, syy / sw - my * my) + 1 / 12, vxy = sxy / sw - mx * my;
  const tr = vxx + vyy, dt = Math.sqrt(Math.max(0, ((vxx - vyy) / 2) ** 2 + vxy * vxy));
  const l1v = tr / 2 + dt, l2v = Math.max(1e-6, tr / 2 - dt);
  const saturated = sat;
  const edge = best.x0 <= 0 || best.y0 <= 0 || best.x1 >= w - 1 || best.y1 >= h - 1;
  const priorF = ex ? clamp(1 - 0.3 * (best.off - 2), 0.3, 1) : 1;
  const conf =
    clamp((snr - 4.5) / 6, 0, 1) * clamp((dominance - 0.4) / 0.4, 0, 1) * (saturated > 0.01 ? clamp(1 - 4 * (saturated - 0.01), 0.4, 1) : 1) * (edge ? 0.7 : 1) * priorF;
  const alt = real
    .filter((c) => c !== best)
    .sort((a, b) => b.score - a.score)
    .slice(0, 3)
    .map((c) => ({ x: (c.px + 0.5) * f - 0.5, y: (c.py + 0.5) * f - 0.5, share: c.score / best.score, peak: c.peak }));
  const hint = snr < 10 && best.peak < 8 ? "wider" : saturated > 0.02 || best.area > 0.12 * w * h ? "narrower" : null;
  return {
    x: (mx + 0.5) * f - 0.5,
    y: (my + 0.5) * f - 0.5,
    conf,
    reason: null,
    ...info,
    size: 2 * Math.sqrt(half / Math.PI) * f,
    elong: Math.sqrt(l1v / l2v),
    angle: 0.5 * Math.atan2(2 * vxy, vxx - vyy),
    spread: { sx: Math.sqrt(vxx) * f, sy: Math.sqrt(vyy) * f },
    saturated,
    clipped: edge,
    hint,
    alt,
    off: best.off,
    gain,
  };
}

// Where the next spot's glow should be, from the spots found so far in string order. done: [{c, x, y, conf?, status?}] (found ones;
// anything without a position, or with status other than "ok", is ignored); c = the next spot's centre pixel. Returns
// {x, y, r, known} (full camera pixels) for findGlow's `expect`, or null with nothing found yet. With two or more found spots it
// carries on in a straight line from the last two; r is 1.6 times the expected step (at least opts.minR, default 80) so a bend still
// lands inside; with one, it stays there with a wide r (opts.wideR, default 240).
export function glowPredict(done, c, opts = {}) {
  const ok = done.filter((a) => a && Number.isFinite(a.x) && Number.isFinite(a.y) && (a.status == null || a.status === "ok") && (a.conf == null || a.conf >= (opts.minConf ?? GLOW.minConf)));
  if (!ok.length) return null;
  const a = ok[ok.length - 1];
  if (ok.length < 2) return { x: a.x, y: a.y, r: opts.wideR ?? 240, known: 1 };
  const b = ok[ok.length - 2], dc = a.c - b.c || 1, t = (c - a.c) / dc;
  const stepLen = Math.hypot(a.x - b.x, a.y - b.y) * Math.abs(t);
  return { x: a.x + (a.x - b.x) * t, y: a.y + (a.y - b.y) * t, r: Math.max(opts.minR ?? 80, 1.6 * stepLen), known: ok.length };
}

// Choose between a glow's candidates once the neighbours are known: spots found with low confidence because another patch of
// similar weight was in the picture (anchor.alt from findGlow) take whichever candidate lies closest to the straight line between
// their confident neighbours. anchors: [{c, x, y, conf, alt?, manual?}] in string order. Returns a new array (same objects where
// nothing changed); a changed one has x, y moved, conf raised to just above minConf, `picked: "alt"`.
export function glowReconsider(anchors, opts = {}) {
  const minConf = opts.minConf ?? GLOW.minConf;
  const solid = (a) => a && a.x != null && a.conf >= 0.6;
  return anchors.map((a, k) => {
    if (!a || a.manual || !a.alt || !a.alt.length || (a.x != null && a.conf >= 0.6)) return a;
    let p = null, q = null;
    for (let j = k - 1; j >= 0 && !p; j--) if (solid(anchors[j])) p = anchors[j];
    for (let j = k + 1; j < anchors.length && !q; j++) if (solid(anchors[j])) q = anchors[j];
    let ex, ey, tol;
    if (p && q) {
      const t = (a.c - p.c) / (q.c - p.c);
      ex = p.x + (q.x - p.x) * t;
      ey = p.y + (q.y - p.y) * t;
      tol = Math.hypot(q.x - p.x, q.y - p.y) * 0.5 + 20;
    } else if (p || q) {
      ex = (p || q).x;
      ey = (p || q).y;
      tol = 150;
    } else return a;
    const options = [...(a.x != null ? [{ x: a.x, y: a.y, share: 1 }] : []), ...a.alt].map((o) => ({ ...o, d: Math.hypot(o.x - ex, o.y - ey) }));
    options.sort((u, v) => u.d - v.d);
    const pick = options[0];
    if (!pick || pick.d > tol || (options[1] && options[1].d < pick.d * 1.5 && options[1].d < tol * 0.5)) return a; // still ambiguous
    if (a.x != null && pick.x === a.x && pick.y === a.y) return { ...a, conf: Math.max(a.conf, minConf + 0.01), picked: "alt" };
    return { ...a, x: pick.x, y: pick.y, conf: Math.max(a.conf || 0, minConf + 0.01), picked: "alt" };
  });
}

// A better width for the next try of a spot, from what the last try showed (findGlow's hint): "wider" +50% (more light), "narrower"
// -35% (less spill); within [GLOW.minWidth, GLOW.maxWidth * 2]. Returns the new width in pixels (integer).
export function glowAdaptWidth(width, hint, opts = {}) {
  const lo = opts.min ?? GLOW.minWidth, hi = opts.max ?? GLOW.maxWidth * 2;
  if (hint === "wider") return clamp(Math.max(width + 1, Math.round(width * 1.5)), lo, hi);
  if (hint === "narrower") return clamp(Math.min(width - 1, Math.round(width * 0.65)), lo, hi);
  return width;
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
// Glow scan from several angles: each run's spots get a 3D place from the multi-view solve; every pixel of the run is then placed
// along the run between them (straight lines in 3D between neighbouring spots, carried on straight beyond the first and last spot).
// count: pixels in the run; known: [{c (pixel position along the run, may be fractional), p: [x, y, z]}]. Returns {pos (Float32Array
// 3 * count), est (Uint8Array: 1 = farther than 0.6 of a spot step from any spot, or beyond the ends), found} or null with < 2 spots.
export function glowRun3D(count, known) {
  const n = Math.max(1, Math.floor(count));
  const k = known.filter((q) => q && q.p && q.p.every(Number.isFinite)).sort((a, b) => a.c - b.c);
  if (k.length < 2) return null;
  const pos = new Float32Array(3 * n), est = new Uint8Array(n);
  const gaps = [];
  for (let j = 1; j < k.length; j++) gaps.push(k[j].c - k[j - 1].c);
  const step = medianOf(gaps) || n;
  let j = 0;
  for (let i = 0; i < n; i++) {
    while (j + 2 < k.length && k[j + 1].c < i) j++;
    const a = k[j], b = k[j + 1], t = (i - a.c) / (b.c - a.c || 1);
    for (let c = 0; c < 3; c++) pos[3 * i + c] = a.p[c] + (b.p[c] - a.p[c]) * t;
    let near = Infinity;
    for (const q of k) near = Math.min(near, Math.abs(q.c - i));
    if (near > 0.6 * step) est[i] = 1;
  }
  return { pos, est, found: k.length };
}


// ======================================================================================================================
// Glow scan in colour: up to three stretches glow at once, one red, one green, one blue, and are told apart by colour, so
// finding all anchors takes a third of the pictures. Needs a short calibration (the whole scanned range lit red, then green,
// then blue) to learn how the camera sees each colour on these surfaces; after that every anchor picture is split into a red,
// a green and a blue glow map (unmixing), and each map goes through findGlow as before. Pictures are RGB, shrunk by downscaleRGB.
// ======================================================================================================================

// block-average RGB down by f: {data: Float32Array (3 per pixel), w, h, f}
export function downscaleRGB(rgb, w, h, f = glowFactor(w)) {
  const sw = Math.max(1, Math.floor(w / f)), sh = Math.max(1, Math.floor(h / f));
  const data = new Float32Array(sw * sh * 3), inv = 1 / (f * f);
  for (let y = 0; y < sh; y++) {
    for (let x = 0; x < sw; x++) {
      let r = 0, g = 0, b = 0;
      for (let j = 0; j < f; j++) {
        let o = ((y * f + j) * w + x * f) * 3;
        for (let i = 0; i < f; i++, o += 3) {
          r += rgb[o];
          g += rgb[o + 1];
          b += rgb[o + 2];
        }
      }
      const q = (y * sw + x) * 3;
      data[q] = r * inv;
      data[q + 1] = g * inv;
      data[q + 2] = b * inv;
    }
  }
  return { data, w: sw, h: sh, f };
}

function inv3(m) {
  const [a, b, c, d, e, f, g, h, i] = m;
  const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
  const det = a * A + b * B + c * C;
  if (Math.abs(det) < 1e-9) return null;
  return { inv: [A / det, -(b * i - c * h) / det, (b * f - c * e) / det, B / det, (a * i - c * g) / det, -(a * f - c * d) / det, C / det, -(a * h - b * g) / det, (a * e - b * d) / det], det };
}

// How the camera sees the three glow colours. lits = [red, green, blue] pictures (downscaleRGB) of the same scene as dark.
// Returns {ok, cols: [[r,g,b] x3 each summing to 1], inv (3x3, row-major), sep, det}: ok only when the three colours look clearly
// different (sep = the smallest distance between two of them).
export function glowColourModel(dark, lits, opts = {}) {
  const cols = [];
  const np = dark.w * dark.h;
  for (let k = 0; k < 3; k++) {
    const lit = lits[k].data, d = new Float32Array(np * 3);
    for (let i = 0; i < d.length; i++) d[i] = lit[i] - dark.data[i];
    const off = [0, 1, 2].map((c) => medianOf(Float32Array.from({ length: Math.ceil(np / 16) }, (_, q) => d[Math.min(np - 1, q * 16) * 3 + c])));
    const s = new Float32Array(np);
    for (let i = 0; i < np; i++) s[i] = Math.max(0, d[i * 3] - off[0]) + Math.max(0, d[i * 3 + 1] - off[1]) + Math.max(0, d[i * 3 + 2] - off[2]);
    const sorted = Float32Array.from(s).sort();
    const top = sorted[Math.floor(sorted.length * 0.995)];
    const thr = Math.max(opts.minDiff ?? 3, 0.4 * top);
    const v = [0, 0, 0];
    for (let i = 0; i < np; i++) {
      if (s[i] < thr) continue;
      for (let c = 0; c < 3; c++) v[c] += Math.max(0, d[i * 3 + c] - off[c]);
    }
    const m = v[0] + v[1] + v[2];
    cols.push(m > 0 ? [v[0] / m, v[1] / m, v[2] / m] : [1 / 3, 1 / 3, 1 / 3]);
  }
  const sep = Math.min(dist3(cols[0], cols[1]), dist3(cols[0], cols[2]), dist3(cols[1], cols[2]));
  // columns are the colours: lit pixel = M * (amount of red, green, blue)
  const M = [cols[0][0], cols[1][0], cols[2][0], cols[0][1], cols[1][1], cols[2][1], cols[0][2], cols[1][2], cols[2][2]];
  const iv = inv3(M);
  const ok = !!iv && sep >= (opts.minSeparation ?? 0.25) && Math.abs(iv.det) > 0.05;
  return { ok, cols, inv: iv ? iv.inv : [1, 0, 0, 0, 1, 0, 0, 0, 1], sep, det: iv ? iv.det : 0 };
}

// The picture split into the amount of red, green and blue glow in each pixel: [Float32Array x3], w x h each (signed: noise stays
// symmetric). The frame-wide shift of auto exposure is taken out per channel first.
export function unmixGlow(dark, lit, model) {
  const np = dark.w * dark.h, d = new Float32Array(np * 3);
  for (let i = 0; i < d.length; i++) d[i] = lit.data[i] - dark.data[i];
  const off = [0, 1, 2].map((c) => medianOf(Float32Array.from({ length: Math.ceil(np / 16) }, (_, q) => d[Math.min(np - 1, q * 16) * 3 + c])));
  const out = [new Float32Array(np), new Float32Array(np), new Float32Array(np)], m = model.inv;
  for (let i = 0; i < np; i++) {
    const r = d[i * 3] - off[0], g = d[i * 3 + 1] - off[1], b = d[i * 3 + 2] - off[2];
    out[0][i] = m[0] * r + m[1] * g + m[2] * b;
    out[1][i] = m[3] * r + m[4] * g + m[5] * b;
    out[2][i] = m[6] * r + m[7] * g + m[8] * b;
  }
  return out;
}

// Find the glow of each colour in `want` (e.g. [0, 1, 2]) in one RGB picture. Returns {glows: [findGlow result | null x3], maps}.
// maps[c] is the unmixed picture of colour c (a luma-like number picture, w x h): pass zeros as `dark` to findGlow / refineTap /
// glowPreview with it.
export function findGlowsColour(dark, lit, model, opts = {}) {
  const maps = unmixGlow(dark, lit, model), zeros = new Float32Array(dark.w * dark.h);
  const glows = [null, null, null];
  for (const c of opts.want ?? [0, 1, 2]) glows[c] = findGlow(zeros, maps[c], dark.w, dark.h, { ...opts, f: opts.f ?? dark.f ?? 1 });
  return { glows, maps };
}

// The sequence for colour glow scanning. plans: [{key, first, plan}] as for glowSchedule. Items:
//   {kind: "dark"}
//   {kind: "calib", ci (0 red, 1 green, 2 blue), start, count, dark}   the whole scanned range in one colour
//   {kind: "segs", segs: [{key, a, start, count, ci}] (1-3, one colour each), dark}
// A run's anchors are lit three at a time, `ceil(A / 3)` pictures apart in the run so the three glows are far from each other.
export function glowScheduleColour(plans, darkEvery = GLOW.darkEvery) {
  const items = [{ kind: "dark" }];
  if (!plans.length) return items;
  const start = Math.min(...plans.map((p) => p.first)), end = Math.max(...plans.map((p) => p.first + p.plan.count));
  for (let ci = 0; ci < 3; ci++) items.push({ kind: "calib", ci, start, count: end - start, dark: 0 });
  let dark = 0, since = 3;
  for (const p of plans) {
    const A = p.plan.anchors.length, T = Math.ceil(A / 3);
    for (let g = 0; g < T; g++) {
      if (since >= darkEvery) {
        items.push({ kind: "dark" });
        dark = items.length - 1;
        since = 0;
      }
      const segs = [];
      for (let q = 0; q < 3; q++) {
        const a = g + q * T;
        if (a < A) segs.push({ key: p.key, a, start: p.first + p.plan.anchors[a].start, count: p.plan.anchors[a].width, ci: q });
      }
      items.push({ kind: "segs", segs, dark });
      since++;
    }
  }
  return items;
}
