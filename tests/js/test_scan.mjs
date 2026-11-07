// Tests for the camera-scan decoder (custom_components/light_show_studio/frontend/light-show-studio-scan.js).
// Run:  node tests/js/test_scan.mjs      (exits non-zero on failure)
// Fake frames are rendered for a known 3D layout and the code sequence, with noise, uneven dot brightness, per-frame
// exposure drift, hidden dots and a stray reflection, then decoded and compared with the truth.
import * as S from "../../custom_components/light_show_studio/frontend/light-show-studio-scan.js";

let fails = 0;
const ok = (cond, msg) => {
  console.log(`${cond ? "ok  " : "FAIL"} ${msg}`);
  if (!cond) fails++;
};

// deterministic random numbers
function rng(seed) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}
function gauss(r) {
  return Math.sqrt(-2 * Math.log(r() + 1e-12)) * Math.cos(6.2832 * r());
}

// a conical spiral "tree": n pixels, x/y/z in 0-1, y up
function tree(n, turns = 5) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1), a = t * turns * 2 * Math.PI, r = 0.42 * (1 - t * 0.85);
    out.push([0.5 + r * Math.cos(a), 0.06 + 0.88 * (1 - t), 0.5 + r * Math.sin(a)]);
  }
  return out;
}

// camera: "front" sees (x, y); "side" (camera to the right of the lights) sees (-z, y). Returns image points (y down).
function project(p, view, W, H) {
  const u = view === "front" ? p[0] : 1 - p[2];
  const sc = H * 0.88; // one scale for both axes, like a real camera
  return [W / 2 + sc * (u - 0.5), H * 0.06 + sc * (1 - p[1])];
}

function render(pts, n, k, o) {
  const { W, H, rand, noise, sigma, hidden, level } = o;
  const gain = 1 + 0.12 * (rand() - 0.5); // auto exposure drift
  const img = new Float32Array(W * H);
  const stray = [[W * 0.93, H * 0.5], [W * 0.05, H * 0.95]]; // reflections: in every frame, so they cancel against the dark frame
  for (const [sx, sy] of stray) splat(img, W, H, sx, sy, 90, 2);
  if (o.straylit && k >= 1) splat(img, W, H, W * 0.5, H * 0.97, 200, 1.5); // only when lights are on: no valid code
  for (let i = 0; i < n; i++) {
    if (hidden.has(i)) continue;
    const l = S.frameLit(i, k);
    if (!l) continue;
    const amp = (l === 2 ? level : level * 0.55) * o.bright[i];
    splat(img, W, H, pts[i][0], pts[i][1], amp, sigma);
  }
  const out = new Uint8ClampedArray(W * H);
  for (let i = 0; i < out.length; i++) out[i] = Math.round(8 + gain * img[i] + gauss(rand) * noise);
  return out;
}
function splat(img, W, H, cx, cy, amp, sigma) {
  const R = Math.ceil(sigma * 3);
  for (let y = Math.max(0, Math.floor(cy) - R); y <= Math.min(H - 1, Math.floor(cy) + R); y++) {
    for (let x = Math.max(0, Math.floor(cx) - R); x <= Math.min(W - 1, Math.floor(cx) + R); x++) {
      img[y * W + x] += amp * Math.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * sigma * sigma));
    }
  }
}

function scene(n, view, seed, extra = {}) {
  const rand = rng(seed), W = 960, H = 540;
  const world = tree(n);
  const pts = world.map((p) => project(p, view, W, H));
  const bright = Array.from({ length: n }, () => 0.6 + 0.4 * rand());
  const hidden = new Set();
  while (hidden.size < Math.round(n * (extra.hide ?? 0.04))) hidden.add(Math.floor(rand() * n));
  const o = { W, H, rand, noise: extra.noise ?? 4, sigma: extra.sigma ?? 1.1, hidden, level: 200, bright, straylit: true };
  const F = S.scanFrames(n);
  const frames = Array.from({ length: F }, (_, k) => render(pts, n, k, o));
  return { world, pts, frames, hidden, W, H };
}

function decode(sc, n) {
  const res = S.decodeScan(sc.frames, n, sc.W, sc.H);
  const fill = S.fillMissing(res.points.map((p) => (p ? [p[0], p[1]] : null)));
  return { res, fill };
}

// ---- code sequence ----
{
  ok(S.scanBits(904) === 10 && S.scanFrames(904) === 22, "904 pixels need 10 bits and 22 frames");
  ok(S.scanBits(150) === 8 && S.scanBits(2) === 1 && S.scanBits(1024) === 10 && S.scanBits(1025) === 11, "bit counts");
  let good = true;
  for (let i = 0; i < 904; i++) {
    let idx = 0;
    for (let b = 0; b < 10; b++) {
      const a = S.frameLit(i, 2 + 2 * b) === 2, c = S.frameLit(i, 3 + 2 * b) === 2;
      if (a === c) good = false; // exactly one of each pair
      if (a) idx |= 1 << b;
    }
    if (idx !== i || S.frameLit(i, 0) !== 0 || S.frameLit(i, 1) !== 1) good = false;
  }
  ok(good, "every pixel's lit/inverse pairs spell out its own index");
}

// ---- front scan, 150 pixels, noise, hidden dots ----
{
  const n = 150, sc = scene(n, "front", 7);
  const { res, fill } = decode(sc, n);
  const expectFound = n - sc.hidden.size;
  ok(res.found >= expectFound - 2 && res.found <= expectFound, `front: found ${res.found} of ${expectFound} visible pixels`);
  let wrong = 0, maxErr = 0;
  for (let i = 0; i < n; i++) {
    const p = res.points[i];
    if (!p) continue;
    const e = Math.hypot(p[0] - sc.pts[i][0], p[1] - sc.pts[i][1]);
    maxErr = Math.max(maxErr, e);
    if (e > 1.5) wrong++;
  }
  ok(wrong === 0, `front: no found pixel is more than 1.5 px off the truth (max ${maxErr.toFixed(2)} px)`);
  let estErr = 0;
  for (const i of sc.hidden) estErr = Math.max(estErr, Math.hypot(fill.x[i] - sc.pts[i][0], fill.y[i] - sc.pts[i][1]));
  ok(estErr < 25, `front: hidden pixels are interpolated near their true places (worst ${estErr.toFixed(1)} px)`);
  ok(res.rejected.badBits + res.rejected.outOfRange + res.rejected.duplicate >= 1, `front: the stray flare was rejected (${JSON.stringify(res.rejected)})`);
}

// ---- 904 pixels (the kitchen), tight spacing, front only ----
{
  const n = 904, rand = rng(3), W = 1920, H = 1080;
  // four rows like the kitchen on a 1080p frame: 2.9 px between pixels
  const rows = [[0, 279, 0.9], [279, 145, 0.62], [424, 192, 0.38], [616, 288, 0.12]];
  const world = [];
  for (const [first, cnt, y] of rows) for (let j = 0; j < cnt; j++) world[first + j] = [0.08 + 0.84 * (j / (cnt - 1)), y, 0.5];
  const pts = world.map((p) => project(p, "front", W, H));
  const bright = Array.from({ length: n }, () => 0.7 + 0.3 * rand());
  const o = { W, H, rand, noise: 3, sigma: 1.1, hidden: new Set([5, 6, 300, 301, 302, 880]), level: 190, bright, straylit: false };
  const frames = Array.from({ length: S.scanFrames(n) }, (_, k) => render(pts, n, k, o));
  const t0 = Date.now();
  const res = S.decodeScan(frames, n, W, H);
  const ms = Date.now() - t0;
  const want = n - o.hidden.size;
  ok(res.found >= want * 0.97, `904 px: found ${res.found} of ${want} (decode took ${ms} ms)`);
  let wrong = 0;
  res.points.forEach((p, i) => p && Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1]) > 1.5 && wrong++);
  ok(wrong === 0, `904 px: no found pixel is misplaced (${wrong} wrong)`);
}

// ---- front + side -> 3D ----
{
  const n = 150;
  const f = scene(n, "front", 11, { hide: 0.03 }), s = scene(n, "side", 12, { hide: 0.03 });
  const fw = S.toWorld(S.fillMissing(decode(f, n).res.points.map((p) => (p ? [p[0], p[1]] : null))));
  const sw = S.toWorld(S.fillMissing(decode(s, n).res.points.map((p) => (p ? [p[0], p[1]] : null))));
  const m = S.mergeViews(fw, sw, {});
  // compare with the truth after the same uniform fit (the box is centred and scaled, axes keep their meaning)
  const truth = tree(n);
  const X = Float64Array.from(truth, (p) => p[0]), Y = Float64Array.from(truth, (p) => p[1]), Z = Float64Array.from(truth, (p) => p[2]);
  const t = S.fitUnitBox(X, Y, Z, new Uint8Array(n), true);
  let worst = 0, sum = 0;
  for (let i = 0; i < n; i++) {
    const e = Math.hypot(m.pos[i * 3] - t.pos[i * 3], m.pos[i * 3 + 1] - t.pos[i * 3 + 1], m.pos[i * 3 + 2] - t.pos[i * 3 + 2]);
    worst = Math.max(worst, e);
    sum += e;
  }
  ok(m.aligned.ok && Math.abs(m.aligned.a - 1) < 0.1, `3D: heights of the two views line up (scale ${m.aligned.a.toFixed(3)} from ${m.aligned.used} shared pixels)`);
  ok(sum / n < 0.02, `3D: mean position error ${(sum / n).toFixed(4)} of the box (worst ${worst.toFixed(3)})`);
  const flipped = S.mergeViews(fw, sw, { flipZ: true });
  let mirrored = 0;
  for (let i = 0; i < n; i++) mirrored += Math.abs(flipped.pos[i * 3 + 2] - (1 - m.pos[i * 3 + 2]));
  ok(mirrored / n < 0.01, "3D: flipping depth mirrors z");
}

// ---- ends missing are extrapolated, interior gaps interpolated ----
{
  const pts = Array.from({ length: 40 }, (_, i) => (i < 4 || i > 36 || (i > 15 && i < 20) ? null : [10 + i * 5, 50]));
  const f = S.fillMissing(pts);
  ok(Math.abs(f.x[17] - (10 + 17 * 5)) < 1e-9 && Math.abs(f.x[0] - 10) < 1e-6 && Math.abs(f.x[39] - 205) < 1e-6, "gaps and ends follow the string order");
  ok(f.est[17] === 1 && f.est[10] === 0 && f.found === 29, "estimated pixels are flagged");
}

// ---- video sync ----
{
  // flashes at t = 4.1, 4.9, 5.7 (800 ms apart) over a noisy dark video sampled every 0.1 s, plus an early stray flicker
  const samples = [];
  for (let t = 0; t < 12; t += 0.1) {
    const inFlash = [4.1, 4.9, 5.7].some((s) => t >= s && t < s + 0.4);
    const flicker = t > 1.0 && t < 1.15;
    samples.push({ t: +t.toFixed(2), v: 10 + Math.sin(t * 7) + (inFlash ? 90 : 0) + (flicker ? 70 : 0) });
  }
  const e = S.findSync(samples);
  ok(e && Math.abs(e.t - 4.1) < 0.11, `video: sync found at ${e ? e.t.toFixed(2) : "none"} s (true 4.10), the lone flicker ignored`);
  ok(S.findSync(samples.map((s) => ({ t: s.t, v: 10 }))) === null, "video: no sync in a flat video");
  const luma = new Uint8ClampedArray(1000).fill(5);
  for (let i = 0; i < 20; i++) luma[i] = 200;
  ok(Math.abs(S.topBrightness(luma) - 200) < 1, "video: top-brightness metric");
  ok(S.videoFrameStart(0, 500) === 3600 && S.videoFrameStart(2, 500) === 4600, "video: frame schedule");
}


if (process.env.SCAN_DEBUG) {
  const n = 150, sc = scene(n, "front", 7), { res } = decode(sc, n);
  const miss = [];
  for (let i = 0; i < n; i++) if (!res.points[i] && !sc.hidden.has(i)) miss.push(i);
  for (const i of miss) {
    let d = 1e9;
    for (let j = 0; j < n; j++) if (j !== i && !sc.hidden.has(j)) d = Math.min(d, Math.hypot(sc.pts[i][0] - sc.pts[j][0], sc.pts[i][1] - sc.pts[j][1]));
    console.log("missing", i, "nearest visible neighbour px", d.toFixed(2));
  }
}
console.log(fails ? `${fails} FAILED` : "all passed");
process.exit(fails ? 1 : 0);
