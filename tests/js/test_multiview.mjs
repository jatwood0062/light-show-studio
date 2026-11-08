// Tests for the multi-angle 3D solver (custom_components/light_show_studio/frontend/light-show-studio-multiview.js).
// Run:  node tests/js/test_multiview.mjs      (exits non-zero on failure)
// Synthetic LED layouts (a cone tree, an L-shaped roofline around a corner, a flat strip) are photographed by 3-5 look-at cameras
// with a focal length the solver does not know, pixel noise, LEDs hidden by facing away or by chance, and a few wrongly decoded
// indexes. The recovered shape is compared with the truth after a similarity (Procrustes) alignment.
import * as M from "../../custom_components/light_show_studio/frontend/light-show-studio-multiview.js";

let fails = 0;
const ok = (cond, msg) => {
  console.log(`${cond ? "ok  " : "FAIL"} ${msg}`);
  if (!cond) fails++;
};
function rng(seed) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}
const gauss = (r) => Math.sqrt(-2 * Math.log(r() + 1e-12)) * Math.cos(6.2832 * r());
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (a) => {
  const n = Math.hypot(...a);
  return a.map((x) => x / n);
};

// ---- layouts: {pts: [[x,y,z]] (y up), nrm: outward normals} ----
function coneTree(n) {
  const pts = [], nrm = [];
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1), a = t * 6 * 2 * Math.PI, r = 0.42 * (1 - 0.85 * t);
    pts.push([r * Math.cos(a), 0.06 + 0.88 * (1 - t), r * Math.sin(a)]);
    nrm.push(unit([Math.cos(a), 0.35, Math.sin(a)]));
  }
  return { pts, nrm, centre: [0, 0.5, 0] };
}
// an L: 150 LEDs along a front wall (z = 0) then 150 along the side wall (x = 1) round the corner, with drops so it is not one plane
function roofline(n) {
  const pts = [], nrm = [], h = n >> 1;
  for (let i = 0; i < n; i++) {
    const wiggle = 0.12 * Math.sin(i * 0.35) + (Math.floor(i / 25) % 2) * 0.12;
    if (i < h) {
      pts.push([(i / (h - 1)) * 1.0, 0.4 + wiggle, 0]);
      nrm.push([0, 0, 1]);
    } else {
      pts.push([1.0, 0.4 + wiggle, -((i - h) / (n - h - 1)) * 0.8]);
      nrm.push([1, 0, 0]);
    }
  }
  return { pts, nrm, centre: [0.5, 0.45, -0.3] };
}
// a flat backlight strip snaking over a wall: 6 rows
function flatStrip(n) {
  const pts = [], nrm = [], per = Math.ceil(n / 6);
  for (let i = 0; i < n; i++) {
    const row = Math.floor(i / per), c = i % per, x = row % 2 ? per - 1 - c : c;
    pts.push([(x / (per - 1)) * 1.2 - 0.6, 0.15 * row, 0]);
    nrm.push([0, 0, 1]);
  }
  return { pts, nrm, centre: [0, 0.37, 0] };
}

// the phone's alpha/beta/gamma for a camera (see deviceRotation in the solver)
function eulOf(cam) {
  // camera axes in world are the rows of R (x right, y down, z forward) -> columns of camera->world
  const Rcw = [cam.R[0], cam.R[3], cam.R[6], cam.R[1], cam.R[4], cam.R[7], cam.R[2], cam.R[5], cam.R[8]];
  // world here is (x, y up, z); the phone's earth frame is (east, north, up): east = x, north = -z, up = y
  const E = [1, 0, 0, 0, 0, -1, 0, 1, 0]; // phone-earth (east, north, up) coordinates of a layout vector: (x, -z, y)
  const Rd = [0, 0, 0, 0, 0, 0, 0, 0, 0];
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) Rd[i * 3 + j] = E[i * 3] * Rcw[j] + E[i * 3 + 1] * Rcw[3 + j] + E[i * 3 + 2] * Rcw[6 + j];
  // device -> earth = camera -> earth * diag(1,-1,-1)
  const D = [Rd[0], -Rd[1], -Rd[2], Rd[3], -Rd[4], -Rd[5], Rd[6], -Rd[7], -Rd[8]];
  // R = Rz(a) Rx(b) Ry(g): D[8] = cos b cos g; D[5] = -sin b; D[6] = -cos b sin g... solve
  const b = Math.asin(D[7]), g = Math.atan2(-D[6], D[8]), a = Math.atan2(-D[1], D[4]);
  const deg = 180 / Math.PI;
  return { alpha: ((a * deg) + 360) % 360, beta: b * deg, gamma: g * deg };
}

// ---- cameras ----
function lookAt(eye, target, rollDeg = 0) {
  const f = unit(sub(target, eye));
  let r = unit(cross(f, [0, 1, 0]));
  let d = cross(f, r);
  if (rollDeg) {
    const c = Math.cos((rollDeg * Math.PI) / 180), s = Math.sin((rollDeg * Math.PI) / 180);
    [r, d] = [r.map((x, k) => c * x + s * d[k]), d.map((x, k) => c * d[k] - s * r[k] * 0 + 0 * x - s * x * 0 - 0)];
    d = cross(f, r);
  }
  const R = [...r, ...d, ...f];
  const t = [-dot(r, eye), -dot(d, eye), -dot(f, eye)];
  return { R, t, eye, f };
}
function shoot(layout, cam, o) {
  const { W, H, rand, noise, fov, miss, wrong } = o;
  const F = 0.5 / Math.tan((fov * Math.PI) / 360), m = Math.max(W, H);
  const points = {};
  layout.pts.forEach((p, i) => {
    const toCam = unit(sub(cam.eye, p));
    if (dot(layout.nrm[i], toCam) < 0.12) return; // faces away
    if (rand() < miss) return; // not decoded
    const q = sub(p, cam.eye);
    const x = dot(cam.R.slice(0, 3), q), y = dot(cam.R.slice(3, 6), q), z = dot(cam.R.slice(6, 9), q);
    if (z <= 0.01) return;
    const u = W / 2 + (F * x * m) / z + gauss(rand) * noise, v = H / 2 + (F * y * m) / z + gauss(rand) * noise;
    if (u < 0 || v < 0 || u >= W || v >= H) return;
    points[i] = [u, v];
  });
  // wrongly decoded: an index given another LED's place
  const keys = Object.keys(points);
  for (let k = 0; k < Math.round(keys.length * wrong); k++) points[keys[Math.floor(rand() * keys.length)]] = [rand() * W, rand() * H];
  return { width: W, height: H, points };
}
function setup(layout, azimuths, o = {}) {
  const rand = rng(o.seed ?? 1);
  const dist = o.dist ?? 3.2, elev = o.elev ?? 0.18;
  const opts = { W: o.W ?? 1920, H: o.H ?? 1080, rand, noise: o.noise ?? 0.6, fov: o.fov ?? 60, miss: o.miss ?? 0.12, wrong: o.wrong ?? 0.01 };
  const cams = azimuths.map((az, k) => {
    const a = ((az + (o.jitter ?? 0) * (rand() - 0.5)) * Math.PI) / 180;
    const c = layout.centre;
    return lookAt([c[0] + dist * Math.sin(a), c[1] + dist * elev * (k % 2 ? 1 : 0.4), c[2] + dist * Math.cos(a)], c);
  });
  return { cams, views: cams.map((c) => shoot(layout, c, opts)) };
}

// error after a similarity fit, as a fraction of the layout's size, over the points the solver triangulated
function shapeError(layout, res) {
  const src = [], dst = [];
  layout.pts.forEach((p, i) => {
    if (res.est[i]) return;
    src.push([res.pos[3 * i], res.pos[3 * i + 1], res.pos[3 * i + 2]]);
    dst.push(p);
  });
  const al = M.alignSimilarity(src, dst);
  let sum = 0, max = 0;
  src.forEach((q, k) => {
    const e = Math.hypot(...sub(al.apply(q), dst[k]));
    sum += e * e;
    max = Math.max(max, e);
  });
  const ext = Math.max(...[0, 1, 2].map((c) => Math.max(...layout.pts.map((p) => p[c])) - Math.min(...layout.pts.map((p) => p[c]))));
  return { rms: Math.sqrt(sum / src.length) / ext, max: max / ext, n: src.length, reflect: al.s < 0 };
}

function check(name, layout, azs, o = {}, tol = {}) {
  const n = layout.pts.length;
  const { views } = setup(layout, azs, o);
  const t0 = Date.now();
  const res = M.solveMultiView(views, { n });
  const ms = Date.now() - t0;
  if (!res.ok) {
    ok(false, `${name}: solver failed (${res.error})`);
    return null;
  }
  const e = shapeError(layout, res);
  const solid = n - res.est.reduce((a, b) => a + b, 0);
  ok(res.views.every((v) => v.ok), `${name}: all ${views.length} views placed (${ms} ms)`);
  ok(e.rms < (tol.rms ?? 0.012) && e.max < (tol.max ?? 0.06), `${name}: shape within ${(e.rms * 100).toFixed(2)}% rms / ${(e.max * 100).toFixed(2)}% worst of the size after Procrustes (${e.n} lights)`);
  ok(res.rms < 2.0, `${name}: reprojection error ${res.rms.toFixed(2)} px rms; focal estimate ${res.fovDeg.toFixed(1)} deg (true ${o.fov ?? 60}, started at 65)`);
  const cov = M.coverage(views, n);
  ok(solid >= 0.88 * cov.multi && solid >= (tol.solid ?? 0.3) * n, `${name}: ${solid} lights triangulated of ${cov.multi} seen in 2+ views (${n} in all); the rest are flagged`);
  return { res, views, e };
}

// ---- the three layouts ----
{
  const r = check("cone tree 300, 4 views", coneTree(300), [0, 55, 110, -60], { seed: 3 });
  if (r) {
    // y up, z towards the first camera (it sits on +z): compare the unit-box result with the truth directly, no rotation allowed
    const lay = coneTree(300), n = 300;
    const solidIdx = [...Array(n).keys()].filter((i) => !r.res.est[i]);
    const lo = [1, 2, 3].map(() => Infinity), hi = [-1, -2, -3].map(() => -Infinity);
    for (const i of solidIdx) for (let c = 0; c < 3; c++) (lo[c] = Math.min(lo[c], lay.pts[i][c])), (hi[c] = Math.max(hi[c], lay.pts[i][c]));
    // scale by the solver's own scale: the span of the y axis (up) is the same in both
    const ys = solidIdx.map((i) => r.res.pos[3 * i + 1]);
    const span = Math.max(...ys) - Math.min(...ys), tspan = hi[1] - lo[1];
    let err = 0;
    for (const i of solidIdx) {
      const tx = 0.5 + (lay.pts[i][0] - (lo[0] + hi[0]) / 2) * (span / tspan), ty = 0.5 + (lay.pts[i][1] - (lo[1] + hi[1]) / 2) * (span / tspan), tz = 0.5 + (lay.pts[i][2] - (lo[2] + hi[2]) / 2) * (span / tspan);
      err += Math.hypot(r.res.pos[3 * i] - tx, r.res.pos[3 * i + 1] - ty, r.res.pos[3 * i + 2] - tz);
    }
    ok(err / solidIdx.length < 0.05, `cone tree: axes agree with the layout directly (x right, y up, z towards the first camera; mean ${(err / solidIdx.length).toFixed(3)})`);
    ok(r.res.pos.every((v) => Number.isFinite(v)) && r.res.count.length === 300, "cone tree: every position is a number");
    ok(r.res.views[0].center[2] > 0.5 && Math.abs(r.res.views[0].center[0] - 0.5) < 0.6, `cone tree: the first camera is on the +z side (${r.res.views[0].center.map((x) => x.toFixed(1)).join(", ")})`);
    const sideSide = r.res.views[3].center[0] < r.res.views[0].center[0] && r.res.views[1].center[0] > r.res.views[0].center[0];
    ok(sideSide, "cone tree: the cameras stood left (-60) and right (+55) of the first one, as they were");
  }
  check("cone tree 300, 3 views", coneTree(300), [0, 50, -50], { seed: 5 }, { rms: 0.02, max: 0.1 });
  check("cone tree 300, 5 views, 0.9 px noise", coneTree(300), [0, 45, 90, -45, -90], { seed: 8, noise: 0.9, jitter: 10 });
}
check("L roofline 300 around a corner, 4 views", roofline(300), [10, 35, 60, 85], { seed: 4, elev: 0.25 });
check("L roofline, 3 views, wider fov", roofline(300), [25, 50, 75], { seed: 9, fov: 72, elev: 0.3 }, { rms: 0.02, max: 0.1 });
check("flat strip 240, 3 views", flatStrip(240), [0, 40, -40], { seed: 6, elev: 0.1 }, { rms: 0.02, max: 0.1, solid: 0.7 });
check("flat strip 240, 4 views, portrait phone", flatStrip(240), [0, 30, -35, 55], { seed: 7, W: 1080, H: 1920, elev: 0.15 }, { rms: 0.02, max: 0.1, solid: 0.7 });

// ---- 1000 lights (the kitchen) at 6 views ----
{
  const n = 1000, lay = coneTree(n);
  const r = check("cone tree 1000, 6 views", lay, [0, 40, 80, -40, -80, 120], { seed: 12, miss: 0.2 }, { rms: 0.012, max: 0.08 });
  void r;
}

// ---- phone orientation: levels the model and is not needed ----
{
  const lay = coneTree(300), n = 300;
  const azs = [0, 50, -50];
  const { cams, views } = setup(lay, azs, { seed: 21 });
  // the phone reports alpha/beta/gamma for each camera: build them from the camera axes (camera -> world = device -> world diag(1,-1,-1))
  const eul = eulOf;
  // the conversion must give back the camera axes
  const o0 = eul(cams[1]), Rb = M.cameraToWorld(o0);
  // Rb maps camera axes to phone-earth (east, north, up); the camera's forward in layout coords is cams[1].f = (x, y, z) -> (x, -z, y)
  const fwd = [Rb[2], Rb[5], Rb[8]], want = [cams[1].f[0], -cams[1].f[2], cams[1].f[1]];
  ok(Math.hypot(...sub(fwd, want)) < 1e-6, "orientation: alpha/beta/gamma turn back into the camera's axes");
  views.forEach((v, k) => (v.orientation = eul(cams[k])));
  const res = M.solveMultiView(views, { n });
  const e = shapeError(lay, res);
  ok(res.ok && e.rms < 0.015 && res.leveled, `orientation: solved with the phone's angles and levelled (${(e.rms * 100).toFixed(2)}% rms)`);
  // levelled: the tree's axis stands along y in the output, so the top is above the bottom; the first tilt-free camera also keeps z towards it
  const solidIdx = [...Array(n).keys()].filter((i) => !res.est[i]);
  const topI = solidIdx.find((i) => i < 60), botI = [...solidIdx].reverse().find((i) => i > 240);
  ok(res.pos[3 * topI + 1] > res.pos[3 * botI + 1], "orientation: the top of the tree (first LED) is above the bottom (last)");
  // a first camera that looks down: the vertical must come out vertical only when the phone's angles are used
  const camsT = [lookAt([0, 1.4, 3], lay.centre), lookAt([2.4, 1.0, 2.0], lay.centre), lookAt([-2.3, 0.7, 2.2], lay.centre)];
  const opts = { W: 1920, H: 1080, rand: rng(33), noise: 0.6, fov: 60, miss: 0.1, wrong: 0.01 };
  const vT = camsT.map((c) => shoot(lay, c, opts));
  const plain = M.solveMultiView(vT, { n });
  const withO = M.solveMultiView(vT.map((v, k) => ({ ...v, orientation: eul(camsT[k]) })), { n });
  // tilt of the output's up axis against the true one, after the best similarity fit
  const tilt = (r) => {
    const ids = [...Array(n).keys()].filter((i) => !r.est[i]);
    const al = M.alignSimilarity(ids.map((i) => [r.pos[3 * i], r.pos[3 * i + 1], r.pos[3 * i + 2]]), ids.map((i) => lay.pts[i]));
    const up = al.R.length ? [al.R[1], al.R[4], al.R[7]] : [0, 1, 0]; // where the output's y axis ends up in the true frame
    return (Math.acos(Math.max(-1, Math.min(1, up[1] / Math.hypot(...up)))) * 180) / Math.PI;
  };
  ok(plain.ok && withO.ok && withO.leveled && !plain.leveled, "orientation: only a solve given the phones' angles is levelled");
  ok(tilt(withO) < 3 && tilt(plain) > 8, `orientation: with the phones' angles the tree stands upright (tilt ${tilt(withO).toFixed(1)} deg; ${tilt(plain).toFixed(1)} deg without, from the downward-looking first camera)`);
}

// ---- fewer than two views, too little overlap, unsolvable ----
{
  const lay = coneTree(300);
  const { views } = setup(lay, [0, 50], { seed: 2 });
  ok(!M.solveMultiView([views[0]], { n: 300 }).ok, "one view is not enough");
  const far = [{ ...views[0], points: Object.fromEntries(Object.entries(views[0].points).filter(([i]) => i < 100)) }, { ...views[1], points: Object.fromEntries(Object.entries(views[1].points).filter(([i]) => i >= 200)) }];
  const r = M.solveMultiView(far, { n: 300 });
  ok(!r.ok && typeof r.error === "string", `views with no lights in common fail with a message (${r.error})`);
  const cov = M.coverage(views, 300);
  ok(cov.multi > 0 && cov.any >= cov.multi && cov.seen.length === 300, `coverage: ${cov.multi} of 300 seen in 2+ views, ${cov.any} in 1+`);
  // Map input works too
  const asMap = views.map((v) => ({ ...v, points: new Map(Object.entries(v.points).map(([k, p]) => [Number(k), p])) }));
  ok(M.solveMultiView(asMap, { n: 300 }).ok, "points given as a Map work as well as plain objects");
}

// ---- lights seen in one view only, or none: placed and flagged ----
{
  const lay = flatStrip(240), n = 240;
  const { views } = setup(lay, [0, 35, -35], { seed: 14, miss: 0.03, elev: 0.1 });
  // LEDs 100-129 seen by the first view only, 210-215 by none
  for (let k = 1; k < 3; k++) for (let i = 100; i < 130; i++) delete views[k].points[i];
  for (const v of views) for (let i = 210; i < 216; i++) delete v.points[i];
  const res = M.solveMultiView(views, { n });
  ok(res.ok && [105, 115, 125, 210, 213].every((i) => res.est[i] === 1), "lights seen once or never are flagged as estimated");
  const good = [...Array(n).keys()].filter((i) => !res.est[i]);
  const al = M.alignSimilarity(good.map((i) => [res.pos[3 * i], res.pos[3 * i + 1], res.pos[3 * i + 2]]), good.map((i) => lay.pts[i]));
  let worstGap = 0, worstOne = 0;
  for (const i of [210, 213, 215]) worstGap = Math.max(worstGap, Math.hypot(...sub(al.apply([res.pos[3 * i], res.pos[3 * i + 1], res.pos[3 * i + 2]]), lay.pts[i])));
  for (const i of [105, 115, 125]) if (views[0].points[i]) worstOne = Math.max(worstOne, Math.hypot(...sub(al.apply([res.pos[3 * i], res.pos[3 * i + 1], res.pos[3 * i + 2]]), lay.pts[i])));
  ok(worstGap < 0.05, `a gap with solid lights either side is bridged along the string (worst ${worstGap.toFixed(3)} of a 1.2 wide strip)`);
  ok(worstOne < 0.12, `a light seen in one view only sits on its ray at the middle depth (worst ${worstOne.toFixed(3)})`);
  ok(res.count[100] <= 1 && res.count[210] === 0 && res.used[100] === 0 && res.used[10] >= 2, "per-light view counts are reported");
  ok(res.pos.every((v) => v >= -0.5 && v <= 1.5), "estimated lights stay near the box");
}


// ---- kitchen glow scan: four hidden runs, sparse glow centres, big noise, outliers ----
// Room 7 x 6 m. An island (2.2 x 0.9 m, turned 25 degrees) in the middle. Four runs of lights:
//  run 0 toe-kick (floor glow, wraps the island, faces at different angles), run 1 island underlip (glows on the island's sides),
//  run 2 under-counter (back wall, glows on the worktop), run 3 crown molding (glows on the ceiling, three walls).
// The scan lights groups of LEDs; only the middle pixel of each glow spot (about every 5th LED) is a point. A glow sits on a surface, so
// its centre moves a little between views (random shift on the surface per view), on top of pixel noise.
function kitchen() {
  const pts = [], nrm = [], runs = [], spots = [];
  const ang = (25 * Math.PI) / 180, ca = Math.cos(ang), sa = Math.sin(ang), cx = 3.5, cz = 3.2, hx = 1.1, hz = 0.45;
  const loc = (x, z) => [cx + ca * x - sa * z, cz + sa * x + ca * z]; // island-local -> room
  const rn = (x, z) => [ca * x - sa * z, sa * x + ca * z];
  // rectangle perimeter at offset d out from the island sides, parameter s in [0,1)
  const perim = (s, d) => {
    const W = 2 * hx + 2 * d, D = 2 * hz + 2 * d, L = 2 * (W + D);
    let u = s * L;
    if (u < W) return { p: [-hx - d + u, -hz - d], n: [0, -1] };
    u -= W;
    if (u < D) return { p: [hx + d, -hz - d + u], n: [1, 0] };
    u -= D;
    if (u < W) return { p: [hx + d - u, hz + d], n: [0, 1] };
    u -= W;
    return { p: [-hx - d, hz + d - u], n: [-1, 0] };
  };
  const addRun = (count, f, spotStep) => {
    const first = pts.length;
    for (let i = 0; i < count; i++) {
      const { p, n } = f(i / count);
      pts.push(p);
      nrm.push(n);
    }
    runs.push([first, count]);
    for (let i = Math.floor(spotStep / 2); i < count; i += spotStep) spots.push(first + i + (i % 3) - 1);
  };
  // toe-kick: on the floor 0.28 m out from each face (glow spreads on the floor), normal up with a slight outward lean
  addRun(130, (s) => {
    const { p, n } = perim(s, 0.28), q = loc(p[0], p[1]), nn = rn(n[0], n[1]);
    return { p: [q[0], 0, q[1]], n: unit([nn[0] * 0.5, 1, nn[1] * 0.5]) };
  }, 4);
  // island underlip: glow on the island side faces at y = 0.8, normal outward
  addRun(110, (s) => {
    const { p, n } = perim(s, 0.0), q = loc(p[0], p[1]), nn = rn(n[0], n[1]);
    return { p: [q[0], 0.8, q[1]], n: [nn[0], 0, nn[1]] };
  }, 4);
  // under-counter: back wall z = 0.6 run, glows on the worktop (y = 0.92), normal up
  addRun(100, (s) => ({ p: [0.8 + 5.4 * s, 0.92, 0.35], n: [0, 1, 0.1] }), 5);
  // crown: ceiling (y = 2.6) glow along the back, left and right walls, normal down
  addRun(120, (s) => {
    const u = s * 3;
    if (u < 1) return { p: [0.3, 2.6, 5.7 - 5.4 * u], n: [0.2, -1, 0] };
    if (u < 2) return { p: [0.3 + 6.4 * (u - 1), 2.6, 0.3], n: [0, -1, 0.2] };
    return { p: [6.7, 2.6, 0.3 + 5.4 * (u - 2)], n: [-0.2, -1, 0] };
  }, 4);
  return { pts, nrm, runs, spots, centre: [3.5, 1.0, 2.5] };
}
function glowViews(lay, cams, o = {}) {
  const rand = rng(o.seed ?? 5), W = o.W ?? 1920, H = o.H ?? 1080, fov = o.fov ?? 65, noise = o.noise ?? 8, shift = o.shift ?? 0.02, out = o.outliers ?? 0.1;
  const F = 0.5 / Math.tan((fov * Math.PI) / 360), m = Math.max(W, H);
  return cams.map((cam) => {
    const points = new Map();
    for (const i of lay.spots) {
      const p = lay.pts[i], toCam = unit(sub(cam.eye, p));
      if (dot(lay.nrm[i], toCam) < (o.cull ?? 0.0)) continue;
      if (rand() < (o.miss ?? 0.1)) continue;
      const pp = [p[0] + gauss(rand) * shift, p[1] + gauss(rand) * shift * 0.5, p[2] + gauss(rand) * shift];
      const q = sub(pp, cam.eye);
      const x = dot(cam.R.slice(0, 3), q), y = dot(cam.R.slice(3, 6), q), z = dot(cam.R.slice(6, 9), q);
      if (z <= 0.05) continue;
      const u = W / 2 + (F * x * m) / z + gauss(rand) * noise, v = H / 2 + (F * y * m) / z + gauss(rand) * noise;
      if (u < 0 || v < 0 || u >= W || v >= H) continue;
      points.set(i, [u, v]);
    }
    for (const k of [...points.keys()]) if (rand() < out) points.set(k, [rand() * W, rand() * H]);
    return { width: W, height: H, points, glow: true };
  });
}
function kitchenCams(az, o = {}) {
  const lay = kitchen(), c = lay.centre;
  return az.map((a0, k) => {
    const a = ((a0 + (o.jitter ?? 0) * (((k * 7919) % 13) / 13 - 0.5)) * Math.PI) / 180, d = (o.dist ?? 3.6) + 0.4 * (k % 2);
    return lookAt([c[0] + d * Math.sin(a), (o.eye ?? 1.5) + 0.15 * (k % 3), c[2] + d * Math.cos(a)], [c[0], o.look ?? 1.2, c[2]]);
  });
}
function glowCheck(name, azs, o = {}, tol = {}) {
  const lay = kitchen(), n = lay.pts.length;
  const cams = kitchenCams(azs, o);
  const views = glowViews(lay, cams, o);
  const sizes = views.map((v) => v.points.size);
  const t0 = Date.now();
  const res = M.solveMultiView(views, { n, fovDeg: 65, runs: lay.runs });
  const ms = Date.now() - t0;
  if (!res.ok) {
    ok(false, `${name}: solver failed (${res.error}) [points per view ${sizes.join("/")}]`);
    return null;
  }
  const e = shapeError(lay, res);
  const placed = res.views.filter((v) => v.ok).length;
  ok(placed >= (tol.views ?? views.length), `${name}: ${placed} of ${views.length} views placed (${ms} ms; ${sizes.join("/")} points per view; fov ${res.fovDeg.toFixed(1)})`);
  ok(e.rms < (tol.rms ?? 0.04) && e.max < (tol.max ?? 0.15), `${name}: shape ${(e.rms * 100).toFixed(2)}% rms / ${(e.max * 100).toFixed(2)}% worst of the size (${e.n} solid lights)`);
  ok(ms < 2000, `${name}: fast enough on a phone (${ms} ms)`);
  return { res, views, e, ms };
}
{
  const lay = kitchen();
  console.log(`kitchen: ${lay.pts.length} lights, ${lay.spots.length} glow spots in ${lay.runs.length} runs`);
  glowCheck("kitchen glow, 4 views, 8 px noise, 10% outliers", [0, 40, -40, 80], { seed: 5, jitter: 12 });
  glowCheck("kitchen glow, 3 views", [0, 45, -45], { seed: 6 }, { rms: 0.05, max: 0.2 });
  glowCheck("kitchen glow, 5 views, 12 px noise", [0, 35, -35, 70, -70], { seed: 7, noise: 12, jitter: 10 }, { rms: 0.05, max: 0.2 });
  glowCheck("kitchen glow, 4 views, low and near-planar", [0, 30, -30, 60], { seed: 8, eye: 1.0, look: 1.0 }, { rms: 0.06, max: 0.25 });
}

// robustness sweep: many random kitchens (cameras, noise, focal length, outliers all varied); most must come out right
{
  const lay = kitchen(), n = lay.pts.length;
  const configs = [
    { name: "4 cams, fov 58", az: [0, 40, -40, 80], o: { fov: 58 } },
    { name: "4 cams, fov 72, 12 px noise", az: [0, 35, -35, 70], o: { fov: 72, noise: 12 } },
    { name: "3 cams, 10% outliers", az: [0, 45, -45], o: {} },
    { name: "5 cams, 20% outliers", az: [0, 30, -30, 60, -60], o: { outliers: 0.2 } },
    { name: "4 cams low and flat-on", az: [-20, 0, 20, 40], o: { eye: 1.1, look: 1.1, fov: 62 } },
  ];
  let total = 0, good = 0, worstMs = 0;
  const bad = [];
  for (const cfg of configs) {
    for (let seed = 1; seed <= 6; seed++) {
      const o = { ...cfg.o, seed: 100 + seed, jitter: 14 };
      const views = glowViews(lay, kitchenCams(cfg.az, o), o);
      const t0 = Date.now();
      const res = M.solveMultiView(views, { n, fovDeg: 65, runs: lay.runs });
      worstMs = Math.max(worstMs, Date.now() - t0);
      total++;
      if (!res.ok) {
        bad.push(`${cfg.name} #${seed}: ${res.error}`);
        continue;
      }
      const e = shapeError(lay, res);
      const placed = res.views.filter((v) => v.ok).length;
      if (placed === views.length && e.rms < 0.05 && e.max < 0.2) good++;
      else bad.push(`${cfg.name} #${seed}: ${placed}/${views.length} placed, ${(e.rms * 100).toFixed(1)}% rms ${(e.max * 100).toFixed(1)}% worst`);
    }
  }
  ok(good >= 0.85 * total, `kitchen sweep: ${good} of ${total} random kitchens solved with all views placed and shape within 5% rms (slowest ${worstMs} ms)`);
  for (const b of bad) console.log("     not solved: " + b);
}

// ---- kitchen: runs, orientation, diagnostics, messages, speed ----
{
  const lay = kitchen(), n = lay.pts.length;
  const o = { seed: 202, jitter: 12 };
  const cams = kitchenCams([0, 40, -40, 80], o);
  const views = glowViews(lay, cams, o);
  const res = M.solveMultiView(views, { n, fovDeg: 65, runs: lay.runs });
  ok(res.ok && Array.isArray(res.warnings) && res.err instanceof Float32Array && res.glow === true, "glow: result carries warnings, per-light pixel error and the glow flag");
  ok(res.views.every((v) => v.ok && v.inliers > 5 && v.shared >= v.inliers && Number.isFinite(v.rms)), `glow: per-view diagnostics (rms ${res.views.map((v) => v.rms.toFixed(1)).join("/")} px, inliers ${res.views.map((v) => v.inliers).join("/")}); noise estimate ${res.noisePx.toFixed(1)} px`);
  // lights never seen sit on their own run: never bridged to the next run
  const solid = (i) => !res.est[i];
  let worst = 0, checked = 0;
  for (const [first, count] of lay.runs) {
    const mine = [];
    for (let i = first; i < first + count; i++) if (solid(i)) mine.push(i);
    if (!mine.length) continue;
    for (const i of [first, first + count - 1]) {
      if (solid(i)) continue;
      let d = Infinity;
      for (const j of mine) d = Math.min(d, Math.hypot(res.pos[3 * i] - res.pos[3 * j], res.pos[3 * i + 1] - res.pos[3 * j + 1], res.pos[3 * i + 2] - res.pos[3 * j + 2]));
      worst = Math.max(worst, d);
      checked++;
    }
  }
  ok(checked > 0 && worst < 0.25, `runs: the end lights of each run (${checked} unseen) stay beside their own run (worst ${worst.toFixed(2)} of the box)`);
  // gravity: the phone's angles level the model and keep it working
  const withO = views.map((v, k) => ({ ...v, orientation: eulOf(cams[k]) }));
  const r2 = M.solveMultiView(withO, { n, fovDeg: 65, runs: lay.runs });
  const e2 = r2.ok ? shapeError(lay, r2) : null;
  ok(r2.ok && r2.leveled && e2.rms < 0.05, `gravity: with orientation the kitchen is levelled and within ${e2 ? (e2.rms * 100).toFixed(1) : "?"}% rms`);
  const medY = (a, b) => {
    const ys = [];
    for (let i = a; i < b; i++) if (!r2.est[i]) ys.push(r2.pos[3 * i + 1]);
    ys.sort((p, q) => p - q);
    return ys[ys.length >> 1];
  };
  const yCrown = medY(320, 440), yFloor = medY(0, 130);
  ok(r2.ok && yCrown > yFloor + 0.3, `gravity: the crown (ceiling) run comes out above the toe-kick (floor) run (${yCrown.toFixed(2)} vs ${yFloor.toFixed(2)})`);
  // without runs the old behaviour still works on the same data
  ok(M.solveMultiView(views, { n, fovDeg: 65 }).ok, "glow views without runs still solve");
  // messages
  const few = M.solveMultiView([views[0], { ...views[1], points: new Map([...views[1].points].slice(0, 3)) }], { n, runs: lay.runs });
  ok(!few.ok && /share/.test(few.error) && few.views.length === 2 && typeof few.views[0].error === "string", `too few common lights: "${few.error}"`);
  const line = (v, k) => ({ ...v, points: new Map([...Array(14).keys()].map((q) => [q, [200 + 120 * q, 500 + k * 7 * q]])) });
  const col = M.solveMultiView([line(views[0], 0), line(views[1], 1)], { n });
  ok(!col.ok && /line/.test(col.error), `all lights on one line: "${col.error}"`);
  // a view that cannot be joined is reported with its reason, the rest still solve
  const rnd = rng(9);
  const junk = { width: 1920, height: 1080, glow: true, points: new Map(Object.keys(Object.fromEntries(views[2].points)).map((k) => [Number(k), [rnd() * 1920, rnd() * 1080]])) };
  const r3 = M.solveMultiView([views[0], views[1], junk, views[3]], { n, runs: lay.runs });
  ok(r3.ok && !r3.views[2].ok && /View 3/.test(r3.views[2].error) && r3.warnings.length > 0, `an unplaceable view is explained: "${r3.views[2].error}"`);
  // speed: 6 views of ~100 glow points
  const cams6 = kitchenCams([0, 30, -30, 60, -60, 90], { seed: 1, jitter: 8 });
  const v6 = glowViews(lay, cams6, { seed: 31, miss: 0.0 });
  const t0 = Date.now();
  const r6 = M.solveMultiView(v6, { n, fovDeg: 65, runs: lay.runs });
  const ms = Date.now() - t0;
  ok(r6.ok && r6.views.filter((v) => v.ok).length >= 5 && ms < 2000, `6 glow views (${v6.map((v) => v.points.size).join("/")} points): ${r6.views.filter((v) => v.ok).length} placed in ${ms} ms`);
}

// ---- the wizard's several-angle bookkeeping (no DOM needed for these) ----
{
  const { ScanWizard } = await import("../../custom_components/light_show_studio/frontend/light-show-studio-scan-wizard.js");
  const lay = flatStrip(240), n = 240;
  const { views } = setup(lay, [0, 35, -35], { seed: 41, elev: 0.1 });
  const w = Object.create(ScanWizard.prototype);
  Object.assign(w, { multiAngles: true, mv: { views: [], redo: -1 }, mods: { multiview: M }, N: n, base: 0, entry: "e", part: "", runs: [{ id: "r", entry_id: "e", first: 0, count: 240 }], scans: {}, view: "front", _orientAtScan: null });
  const asRes = (v) => ({ points: Array.from({ length: n }, (_, i) => (v.points[i] ? [v.points[i][0], v.points[i][1], 1] : null)), found: Object.keys(v.points).length });
  w._addAngle(asRes(views[0]), 1920, 1080);
  ok(w.mv.views.length === 1 && w.mv.views[0].points.size === Object.keys(views[0].points).length, "wizard: a decoded scan becomes a view");
  ok(w._backStep(false) === "angles" && w._backStep(true) === "angles", "wizard: Back and a failed scan return to the angles screen in a several-angle scan");
  ok(/45 degrees to the left/.test(w._angleHint(1)), `wizard: first hint: ${w._angleHint(1)}`);
  w._addAngle(asRes(views[1]), 1920, 1080);
  w._addAngle(asRes(views[2]), 1920, 1080);
  const sol = await w._solveViews();
  ok(sol.ok && w.mv.result === sol && (await w._solveViews()) === sol, "wizard: the solve is cached until another angle is added");
  w._addAngle(asRes(views[2]), 1920, 1080);
  ok(w.mv.result === null && w.mv.views.length === 4, "wizard: adding an angle clears the cached solve");
  w.mv.redo = 3;
  w._addAngle(asRes(views[1]), 1920, 1080);
  ok(w.mv.views.length === 4 && w.mv.redo === -1, "wizard: redoing an angle replaces it");
  const az = w._azimuths(sol).map((a) => Math.round(a));
  ok(Math.abs(az[0]) < 8 && az[1] * az[2] < 0 && Math.abs(Math.abs(az[1]) - 35) < 8, `wizard: camera azimuths about the lights come out as stood (${az.join(", ")} for 0, 35, -35)`);
  w.mv.result = sol;
  ok(/degrees to the (left|right)/.test(w._angleHint(3)), `wizard: the next hint names a side and an angle: ${w._angleHint(3)}`);
  ok(w._scanIdx().length === 240, "wizard: all pixels of the runs are in the scan");
  w.multiAngles = false;
  ok(w._backStep(false) === "pick" && w._backStep(true) === "pick", "wizard: the single scan path still goes Back to the first screen");
  w.view = "side";
  ok(w._backStep(false) === "review", "wizard: ... and back to the review from a side scan");
}

console.log(fails ? `\n${fails} FAILED` : "\nall passed");
process.exit(fails ? 1 : 0);
