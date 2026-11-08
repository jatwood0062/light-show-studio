// Tests for the glow scan maths (glow part of custom_components/light_show_studio/frontend/light-show-studio-scan.js).
// Run:  node tests/js/test_glow.mjs      (exits non-zero on failure)
// A hidden strip is simulated: a curved run of pixels whose lit stretch makes a soft, blurred puddle of varying size and
// brightness somewhere near it. Frames get noise, exposure drift and stray reflections; some puddles are occluded or replaced by
// a wrong blob. The anchors are found, interpolated along the string, and compared with the truth.
import * as S from "../../custom_components/light_show_studio/frontend/light-show-studio-scan.js";

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

const W = 960, H = 540;
// the curved run: pixel i of n sits at path(i / (n - 1)) in the picture (y down)
const path = (t) => [70 + 820 * t, 270 + 150 * Math.sin(2 * Math.PI * t * 1.2 + 0.4) - 40 * t];

function splat(img, cx, cy, amp, sx, sy, th = 0) {
  const R = Math.ceil(Math.max(sx, sy) * 3.2), c = Math.cos(th), s = Math.sin(th);
  for (let y = Math.max(0, Math.floor(cy) - R); y <= Math.min(H - 1, Math.floor(cy) + R); y++) {
    for (let x = Math.max(0, Math.floor(cx) - R); x <= Math.min(W - 1, Math.floor(cx) + R); x++) {
      const u = (x - cx) * c + (y - cy) * s, v = -(x - cx) * s + (y - cy) * c; // rotated by th (y down)
      img[y * W + x] += amp * Math.exp(-(u * u / (2 * sx * sx) + v * v / (2 * sy * sy)));
    }
  }
}

// one camera frame (Uint8 luma): `puddles` = [[x, y, amp, sx, sy]] lit now
function frame(puddles, rand, o = {}) {
  const img = new Float32Array(W * H);
  splat(img, W * 0.93, H * 0.5, 80, 2, 2); // reflections that are in every frame
  splat(img, W * 0.06, H * 0.9, 60, 3, 3);
  for (const p of o.bg || []) splat(img, ...p); // static light: in every frame, scaled by the gain with everything else
  for (const p of puddles) splat(img, ...p);
  const gain = o.gain ?? 1 + (o.drift ?? 0.1) * (rand() - 0.5);
  const out = new Uint8ClampedArray(W * H);
  for (let i = 0; i < out.length; i++) out[i] = Math.round(10 + (o.ramp ?? 0) + gain * img[i] + gauss(rand) * (o.noise ?? 4));
  return out;
}

// Runs the whole glow scan of a run of n pixels: plan, "camera" frames, find each anchor, solve. Returns the truth next to it.
function scan(n, seed, o = {}) {
  const rand = rng(seed);
  const plan = S.glowPlan(n, o.plan);
  const truth = Array.from({ length: n }, (_, i) => path(i / (n - 1)));
  const dark = S.downscaleLuma(frame([], rand, o), W, H);
  const found = [];
  const bad = new Set(o.occluded || []), wrong = new Set(o.wrong || []);
  for (const a of plan.anchors) {
    // the glow of the lit stretch: the pixels' places spread a little (the glow lands where the light falls, on a wall or counter)
    let mx = 0, my = 0;
    for (let k = 0; k < a.width; k++) {
      mx += truth[a.start + k][0];
      my += truth[a.start + k][1];
    }
    mx /= a.width;
    my /= a.width;
    const size = 14 + 22 * rand(), amp = (o.dim ? 5 : 22) + (o.dim ? 5 : 50) * rand();
    let puddles = [[mx, my, amp, size * 1.3, size]];
    if (rand() < 0.3) puddles.push([mx + 90 * (rand() - 0.5), my + 60, amp * 0.18, 8, 8]); // a second, fainter bounce
    if (bad.has(a.i)) puddles = [[mx, my, 1.0, 20, 20]]; // hidden: nearly nothing reaches the camera
    if (wrong.has(a.i)) puddles = [[mx + 260, my + (a.i % 2 ? 190 : -190), amp, 18, 18]]; // the light goes somewhere else entirely
    if (o.speck) puddles.push([40 + 800 * rand(), 40 + 400 * rand(), 255, 1.2, 1.2]); // a glint
    const lit = S.downscaleLuma(frame(puddles, rand, o), W, H);
    const g = S.findGlow(dark.data, lit.data, lit.w, lit.h, { f: lit.f });
    found.push({ c: a.c, x: g.x, y: g.y, conf: g.conf, reason: g.reason, a, g });
  }
  const sol = S.solveGlow(n, found);
  return { n, plan, truth, found, sol };
}

function errors(r, skip = new Set()) {
  let max = 0, sum = 0, cnt = 0;
  for (let i = 0; i < r.n; i++) {
    if (r.sol.est[i] || skip.has(i)) continue;
    const e = Math.hypot(r.sol.x[i] - r.truth[i][0], r.sol.y[i] - r.truth[i][1]);
    max = Math.max(max, e);
    sum += e;
    cnt++;
  }
  return { max, mean: sum / Math.max(1, cnt), cnt };
}

// ---- the plan ----
{
  for (const n of [279, 145, 192, 288, 904, 40, 12, 3, 1]) {
    const p = S.glowPlan(n);
    const starts = p.anchors.map((a) => a.start);
    const sorted = starts.every((s, k) => k === 0 || s > starts[k - 1]);
    const inside = p.anchors.every((a) => a.start >= 0 && a.start + a.width <= n);
    ok(sorted && inside && p.anchors.length >= 1, `plan ${n}: ${p.anchors.length} anchors of ${p.width} px, ordered and inside the run`);
    if (n >= 100) ok(p.anchors.length >= 16 && p.anchors.length <= 24 && p.width >= 3 && p.width <= 6, `plan ${n}: 16-24 anchors, small spots of 3-6 px (${p.anchors.length} x ${p.width})`);
  }
  const p = S.glowPlan(279);
  ok(p.anchors[0].start === 0 && p.anchors.at(-1).start + p.width === 279, "plan: the first and last stretches sit at the run's ends");
  const q = S.glowPlan(200, { anchors: 10, width: 20 });
  ok(q.anchors.length === 10 && q.width === 20, "plan: both knobs can be set");
  // the schedule
  const sch = S.glowSchedule([{ key: "a", first: 0, plan: S.glowPlan(279) }, { key: "b", first: 279, plan: S.glowPlan(145) }]);
  const segs = sch.filter((x) => x.kind === "seg");
  ok(sch[0].kind === "dark" && segs.length === S.glowPlan(279).anchors.length + S.glowPlan(145).anchors.length, "schedule: a dark frame first, then every anchor");
  ok(sch.filter((x) => x.kind === "dark").length >= 3 && segs.every((s) => sch[s.dark].kind === "dark" && s.dark < sch.indexOf(s)), "schedule: dark references are refreshed and each anchor points at an earlier one");
  ok(segs.filter((s) => s.key === "b").every((s) => s.start >= 279 && s.start + s.count <= 424), "schedule: stretches are pixels of the light (run b starts at 279)");
}

// ---- one clean puddle ----
{
  const rand = rng(5);
  const dark = S.downscaleLuma(frame([], rand), W, H), lit = S.downscaleLuma(frame([[400, 300, 40, 30, 20]], rand), W, H);
  const g = S.findGlow(dark.data, lit.data, lit.w, lit.h, { f: lit.f });
  ok(!g.reason && Math.hypot(g.x - 400, g.y - 300) < 3 && g.conf > 0.7, `one puddle: centre (${g.x?.toFixed(1)}, ${g.y?.toFixed(1)}) conf ${g.conf.toFixed(2)}`);
  // nothing lit, a glint only, a very dim puddle, a puddle over everything
  const none = S.downscaleLuma(frame([], rng(6)), W, H);
  const r1 = S.findGlow(dark.data, none.data, none.w, none.h, { f: none.f });
  ok(r1.x === null && (r1.reason === "none" || r1.reason === "weak"), `nothing lit: rejected (${r1.reason})`);
  const spk = S.downscaleLuma(frame([[300, 200, 255, 1.2, 1.2]], rng(7)), W, H);
  const r2 = S.findGlow(dark.data, spk.data, spk.w, spk.h, { f: spk.f });
  ok(r2.x === null, `a lone glint is ignored (${r2.reason})`);
  const faint = S.downscaleLuma(frame([[500, 250, 1.2, 40, 30]], rng(8)), W, H);
  const r3 = S.findGlow(dark.data, faint.data, faint.w, faint.h, { f: faint.f });
  ok(r3.x === null || r3.conf < S.GLOW.minConf, `a barely-there glow is rejected or low (${r3.reason}, conf ${r3.conf.toFixed(2)})`);
  const flood = S.downscaleLuma(frame([[480, 270, 40, 400, 300]], rng(9)), W, H);
  const r4 = S.findGlow(dark.data, flood.data, flood.w, flood.h, { f: flood.f });
  ok(r4.x === null || r4.conf < S.GLOW.minConf, `light everywhere is not a place (${r4.reason}, conf ${r4.conf.toFixed(2)})`);
  // a bright glint beside a real puddle must not pull the centre
  const mix = S.downscaleLuma(frame([[400, 300, 30, 28, 20], [800, 100, 255, 1.2, 1.2]], rng(10)), W, H);
  const r5 = S.findGlow(dark.data, mix.data, mix.w, mix.h, { f: mix.f });
  ok(!r5.reason && Math.hypot(r5.x - 400, r5.y - 300) < 4, `glint + puddle: the puddle wins (${r5.x?.toFixed(1)}, ${r5.y?.toFixed(1)})`);
  // two similar puddles: the call is not confident
  const two = S.downscaleLuma(frame([[250, 250, 30, 24, 18], [700, 300, 29, 24, 18]], rng(11)), W, H);
  const r6 = S.findGlow(dark.data, two.data, two.w, two.h, { f: two.f });
  ok(r6.conf < S.GLOW.minConf, `two equal puddles: low confidence (${r6.conf.toFixed(2)})`);
}

// ---- whole runs along a curve ----
for (const [n, seed] of [[279, 1], [145, 2], [192, 3], [288, 4]]) {
  const r = scan(n, seed);
  const e = errors(r);
  const okAnchors = r.sol.anchors.filter((a) => a.status === "ok").length;
  ok(okAnchors === r.plan.anchors.length, `run ${n}: every anchor found (${okAnchors}/${r.plan.anchors.length})`);
  ok(e.max < 6 && e.mean < 2.5, `run ${n}: pixel places within ${e.max.toFixed(1)} px of the truth (mean ${e.mean.toFixed(2)}) on a ${path(1)[0] - path(0)[0]} px wide curve`);
  // smooth and in string order: steps between neighbouring pixels are small and never jump back
  let maxStep = 0, jumps = 0;
  const idealStep = Math.hypot(r.truth[1][0] - r.truth[0][0], r.truth[1][1] - r.truth[0][1]);
  for (let i = 1; i < n; i++) {
    const st = Math.hypot(r.sol.x[i] - r.sol.x[i - 1], r.sol.y[i] - r.sol.y[i - 1]);
    maxStep = Math.max(maxStep, st);
    const along = (r.sol.x[i] - r.sol.x[i - 1]) * (r.truth[i][0] - r.truth[i - 1][0]); // along the run's main direction
    if (along < -0.05) jumps++;
  }
  ok(maxStep < idealStep * 3 + 2 && jumps === 0, `run ${n}: smooth (largest step ${maxStep.toFixed(1)} px vs about ${idealStep.toFixed(1)}), nothing doubles back`);
}

// ---- noisy, dim, specks ----
{
  const r = scan(192, 21, { noise: 5, dim: true, speck: true, drift: 0.2 });
  const e = errors(r);
  const okA = r.sol.anchors.filter((a) => a.status === "ok").length;
  ok(okA >= r.plan.anchors.length - 1 && e.max < 14, `dim, noisy, with glints: ${okA}/${r.plan.anchors.length} anchors, worst pixel ${e.max.toFixed(1)} px off, mean ${e.mean.toFixed(1)}`);
}

// ---- occluded anchors are flagged and bridged ----
{
  const occ = [4, 5, 11];
  const r = scan(279, 31, { occluded: occ });
  const st = r.sol.anchors.map((a) => a.status);
  const flagged = occ.filter((k) => st[k] !== "ok");
  ok(flagged.length === occ.length, `occluded anchors ${occ} are flagged (${occ.map((k) => st[k]).join(", ")})`);
  ok(st.filter((s) => s === "ok").length === r.plan.anchors.length - occ.length, "and nothing else is");
  const e = errors(r);
  ok(e.max < 12, `pixels near found anchors stay accurate (${e.max.toFixed(1)} px)`);
  // the gap is bridged by interpolation, marked as guessed, and still near the path
  let gapErr = 0, guessed = 0;
  for (let i = 0; i < r.n; i++) {
    if (!r.sol.est[i]) continue;
    guessed++;
    gapErr = Math.max(gapErr, Math.hypot(r.sol.x[i] - r.truth[i][0], r.sol.y[i] - r.truth[i][1]));
  }
  ok(guessed > 0 && guessed < r.n * 0.35 && gapErr < 25, `${guessed} pixels are marked guessed, worst ${gapErr.toFixed(1)} px off`);
}

// ---- ends missing ----
{
  const r = scan(192, 41, { occluded: [0, 1] });
  ok(r.sol.anchors[0].status !== "ok" && r.sol.anchors[1].status !== "ok" && r.sol.anchors[2].status === "ok", "first two anchors hidden: flagged");
  const e0 = Math.hypot(r.sol.x[0] - r.truth[0][0], r.sol.y[0] - r.truth[0][1]);
  ok(r.sol.est[0] === 1 && e0 < 140, `the run's start is extrapolated in a straight line and marked guessed (${e0.toFixed(0)} px off)`);
}

// ---- a wrong blob is an outlier, not a place ----
{
  const r = scan(279, 51, { wrong: [9] });
  const st = r.sol.anchors[9].status;
  ok(st === "outlier" || st === "low", `an anchor that lit the wrong place is flagged (${st})`);
  const e = errors(r);
  ok(e.max < 14, `and does not pull the path (worst ${e.max.toFixed(1)} px)`);
}

// ---- manual taps count; low-confidence ones are only used when confirmed ----
{
  const anchors = [0, 1, 2, 3, 4, 5].map((k) => ({ c: 4 + k * 20, x: 100 + k * 50, y: 200, conf: 0.9 }));
  anchors[2] = { c: 44, x: 200, y: 200, conf: 0.1 };
  let s = S.solveGlow(110, anchors);
  ok(s.anchors[2].status === "low" && s.found === 5 && Math.abs(s.x[44] - 200) < 1, "a weak anchor is not used, the line passes by it anyway");
  anchors[2].manual = true;
  s = S.solveGlow(110, anchors);
  ok(s.anchors[2].status === "ok" && s.found === 6, "a person's tap makes it ok");
  anchors[3] = { c: 64 };
  ok(S.solveGlow(110, anchors).anchors[3].status === "missing", "no position = missing");
  ok(S.solveGlow(110, [{ c: 4, x: 1, y: 1, conf: 1 }, { c: 24 }]).ok === false, "fewer than two anchors: not solvable");
}

// ---- a person's tap is kept even when it is off the line ----
{
  const anchors = [0, 1, 2, 3, 4, 5].map((k) => ({ c: k * 20, x: 100 + k * 50, y: 200, conf: 0.9 }));
  anchors[3] = { c: 60, x: 250, y: 600, conf: 0.9 };
  ok(S.solveGlow(101, anchors).anchors[3].status === "outlier", "an automatic spot far off the line is dropped");
  anchors[3].manual = true;
  ok(S.solveGlow(101, anchors).anchors[3].status === "ok", "the same spot tapped by a person is kept");
}

// ---- a U-turn path: monotone interpolation does not overshoot ----
{
  const anchors = [];
  const pts = [[100, 100], [300, 100], [500, 100], [500, 200], [300, 200], [100, 200]];
  pts.forEach((p, k) => anchors.push({ c: k * 20, x: p[0], y: p[1], conf: 1 }));
  const s = S.solveGlow(101, anchors);
  let over = 0;
  for (let i = 0; i < 101; i++) if (s.x[i] < 99 || s.x[i] > 501 || s.y[i] < 99 || s.y[i] > 201) over++;
  ok(over === 0 && s.anchors.every((a) => a.status === "ok"), "a sharp turn stays inside the anchors' box and no anchor is called wrong");
}

// ---- tapping ----
{
  const rand = rng(61);
  const dark = S.downscaleLuma(frame([], rand), W, H), lit = S.downscaleLuma(frame([[300, 350, 8, 30, 25]], rand), W, H);
  const t = S.refineTap(dark.data, lit.data, lit.w, lit.h, 318, 340, { f: lit.f });
  ok(t.snapped && Math.hypot(t.x - 300, t.y - 350) < 6, `a tap near a glow snaps to its centre (${t.x.toFixed(0)}, ${t.y.toFixed(0)})`);
  const t2 = S.refineTap(dark.data, lit.data, lit.w, lit.h, 800, 80, { f: lit.f });
  ok(!t2.snapped && t2.x === 800 && t2.y === 80, "a tap on nothing stays where it is");
  const prev = S.glowPreview(dark.data, lit.data, lit.w, lit.h);
  ok(prev.length === lit.w * lit.h * 4 && prev[(Math.round(350 / lit.f) * lit.w + Math.round(300 / lit.f)) * 4] > 150, "the preview shows the glow bright");
}


// ======================================================================================================================
// Hard cases: indirect light on surfaces seen at an angle (kitchen toe-kick / crown molding).
// ======================================================================================================================
const mkdark = (rand, o) => S.downscaleLuma(frame([], rand, o), W, H);
const glowOf = (puddles, seed, o = {}, g = {}) => {
  const rand = rng(seed), dark = mkdark(rand, o.darkOpts || o), lit = S.downscaleLuma(frame(puddles, rand, o), W, H);
  const dark2 = o.darkAfter ? mkdark(rand, o.darkAfter) : null;
  return S.findGlow(dark.data, lit.data, lit.w, lit.h, { f: lit.f, ...(dark2 ? { darkAfter: dark2.data } : {}), ...g });
};
const dist = (g, x, y) => Math.hypot(g.x - x, g.y - y);

// ---- a floor patch seen at a steep angle: long, soft, rotated, with a tail running away from the strip ----
{
  // core near the strip, a long faint tail further along the floor (the far part of the light cone)
  const patch = (cx, cy, th, k = 1) => [[cx, cy, 30 * k, 40, 11, th], [cx + 70 * Math.cos(th), cy + 70 * Math.sin(th), 9 * k, 90, 26, th]];
  const th = 0.35;
  const g = glowOf(patch(420, 380, th), 101);
  // the brightest part is the answer; the tail pulls a plain centroid out by ~25 px
  ok(!g.reason && dist(g, 420, 380) < 18 && g.conf >= S.GLOW.minConf, `oblique floor patch: centre within ${dist(g, 420, 380).toFixed(1)} px of its bright core (conf ${g.conf.toFixed(2)})`);
  ok(g.elong > 1.8, `oblique floor patch: reports it is elongated (${g.elong.toFixed(1)}:1)`);
  ok(Math.abs(Math.atan(Math.tan(g.angle - th))) < 0.35, `oblique floor patch: major axis angle ${g.angle.toFixed(2)} vs ${th}`);
  // dimmer, with noise
  const g2 = glowOf(patch(600, 200, -0.5, 0.35), 102, { noise: 5 });
  ok(!g2.reason && dist(g2, 600, 200) < 22, `dim oblique patch: found (${g2.reason}), ${g2.x ? dist(g2, 600, 200).toFixed(1) : "-"} px off`);
  // a wide ceiling wash (crown molding): very large, soft; centre still near the brightest
  const g3 = glowOf([[480, 120, 14, 150, 25, 0.0], [480, 110, 8, 260, 50, 0]], 103);
  ok(!g3.reason && Math.abs(g3.x - 480) < 20 && Math.abs(g3.y - 118) < 14, `wide ceiling wash: centre (${g3.x?.toFixed(0)}, ${g3.y?.toFixed(0)})`);
}

// ---- a glossy floor: the patch plus a sharper, weaker mirror image below it ----
{
  const real = [400, 260, 30, 34, 22, 0], refl = [400, 410, 14, 14, 9, 0], streak = [405, 340, 6, 4, 70, 0];
  const g = glowOf([real, refl, streak], 111);
  ok(!g.reason && dist(g, 400, 260) < 8, `glossy floor: the patch wins over its reflection (${dist(g, 400, 260).toFixed(1)} px off, conf ${g.conf.toFixed(2)})`);
  // a reflection as strong as the patch: the call is flagged as unsure, and the alternative is offered
  const g2 = glowOf([[300, 240, 28, 26, 20, 0], [620, 330, 26, 26, 20, 0]], 112);
  ok(g2.conf < S.GLOW.minConf && g2.alt.length >= 1, `equal reflection: low confidence (${g2.conf.toFixed(2)}) and the other candidate is listed (${g2.alt.length})`);
  const alt = g2.alt[0];
  ok(Math.min(dist(g2, 300, 240), dist(g2, 620, 330)) < 8 && Math.min(dist(alt, 300, 240), dist(alt, 620, 330)) < 8, "equal reflection: both candidates sit on the real patches");
  // the prior (previous spot) resolves it
  const g3 = glowOf([[300, 240, 28, 26, 20, 0], [620, 330, 26, 26, 20, 0]], 112, {}, { expect: { x: 640, y: 320, r: 120 } });
  ok(!g3.reason && dist(g3, 620, 330) < 8 && g3.conf >= S.GLOW.minConf, `equal reflection + expected position: picks the expected one (conf ${g3.conf.toFixed(2)})`);
  // a gate hides the other completely
  const g4 = glowOf([[300, 240, 28, 26, 20, 0], [620, 330, 26, 26, 20, 0]], 112, {}, { gate: { x: 300, y: 240, r: 150 } });
  ok(!g4.reason && dist(g4, 300, 240) < 8 && g4.conf >= S.GLOW.minConf, "equal reflection + gate: only the gated one counts");
}

// ---- two patches at once, touching (the toe-kick lights two faces of the island) ----
{
  const two = [[360, 300, 26, 30, 18, 0.2], [470, 330, 22, 30, 18, 0.2]]; // a valley between them, one connected region
  const g = glowOf(two, 121);
  const nearA = dist(g, 360, 300) < 12, nearB = dist(g, 470, 330) < 12;
  ok(g.reason || nearA || nearB, `touching double patch: not reported as one blob in the middle (${g.x?.toFixed(0)}, ${g.y?.toFixed(0)})`);
  ok(g.conf < 0.75 && g.alt.length >= 1, `touching double patch: unsure (conf ${g.conf.toFixed(2)}), other face offered (${g.alt.length})`);
  const gB = glowOf(two, 121, {}, { expect: { x: 480, y: 335, r: 70 } });
  ok(!gB.reason && dist(gB, 470, 330) < 12 && gB.conf > g.conf, `touching double patch + expected: the right face (${gB.conf.toFixed(2)} > ${g.conf.toFixed(2)})`);
  // an elongated single patch with a noisy ridge must NOT be split in two
  const one = glowOf([[420, 300, 30, 70, 14, 0.3]], 122, { noise: 6 });
  ok(!one.reason && one.alt.length === 0 && one.conf > 0.6, `one long patch is not split (alt ${one.alt.length}, conf ${one.conf.toFixed(2)})`);
}

// ---- along a run: each spot lights its patch and a second one; prediction + reconsideration keep the path straight ----
{
  const run = (usePredict, seed) => {
    const rand = rng(seed), n = 288, plan = S.glowPlan(n);
    const truth = Array.from({ length: n }, (_, i) => path(i / (n - 1)));
    const dark = mkdark(rand, {});
    const found = [];
    for (const a of plan.anchors) {
      const [mx, my] = truth[Math.round(a.c)];
      const big = rand() < 0.5; // the decoy is sometimes stronger
      const pu = [[mx, my, 26 * (big ? 0.8 : 1), 28, 18, 0.3], [mx + (a.i % 2 ? 200 : -200), my - 110 + 40 * rand(), 26 * (big ? 1.1 : 0.7), 28, 18, 0.3]];
      const lit = S.downscaleLuma(frame(pu, rand, {}), W, H);
      const ex = usePredict ? S.glowPredict(found, a.c, { minR: 90 }) : null;
      const g = S.findGlow(dark.data, lit.data, lit.w, lit.h, { f: lit.f, ...(ex ? { expect: ex } : {}) });
      found.push({ c: a.c, x: g.x, y: g.y, conf: g.conf, alt: g.alt, reason: g.reason, status: g.reason ? "missing" : "ok" });
    }
    const fixed = S.glowReconsider(found);
    let wrong = 0;
    fixed.forEach((f, k) => {
      const [tx, ty] = truth[Math.round(plan.anchors[k].c)];
      if (f.x == null || Math.hypot(f.x - tx, f.y - ty) > 25) wrong++;
    });
    return { wrong, total: found.length, fixed };
  };
  let w0 = 0, w1 = 0, tot = 0;
  for (const seed of [201, 202, 203]) {
    w0 += run(false, seed).wrong;
    const r = run(true, seed);
    w1 += r.wrong;
    tot += r.total;
  }
  ok(w1 <= 0.12 * tot, `decoy patches: with prediction + reconsideration ${w1}/${tot} spots wrong`);
  ok(w1 <= w0, `decoy patches: prediction is no worse than none (${w1} vs ${w0})`);
  // predictions
  const a = [{ c: 10, x: 100, y: 100, conf: 1 }, { c: 30, x: 200, y: 140, conf: 1 }];
  const p = S.glowPredict(a, 50, { minR: 10 });
  ok(Math.abs(p.x - 300) < 1e-6 && Math.abs(p.y - 180) < 1e-6 && p.r > 100, `glowPredict extrapolates in a line (${p.x}, ${p.y}, r ${p.r.toFixed(0)})`);
  ok(S.glowPredict([], 5) === null && S.glowPredict([a[0]], 20).r >= 200, "glowPredict: nothing found yet = no prediction; one spot = wide window");
  ok(S.glowPredict([a[0], { c: 30, x: 5, y: 5, conf: 0.1 }], 50).known === 1, "glowPredict ignores low-confidence spots");
}

// ---- very dim patches, lots of noise ----
{
  let hits = 0, worst = 0;
  const N = 12;
  for (let k = 0; k < N; k++) {
    const cx = 200 + 55 * k, cy = 150 + 20 * (k % 5);
    const g = glowOf([[cx, cy, 2.6, 38, 26, 0.2]], 130 + k, { noise: 5 });
    if (!g.reason && g.conf >= S.GLOW.minConf) {
      hits++;
      worst = Math.max(worst, dist(g, cx, cy));
    }
  }
  ok(hits >= N - 2 && worst < 22, `very dim (peak ~2.6 of 255, noise 5): found ${hits}/${N}, worst ${worst.toFixed(1)} px`);
  const none = glowOf([], 150, { noise: 5 });
  ok(none.x === null, `very dim: an unlit picture still finds nothing (${none.reason})`);
  const g = glowOf([[480, 270, 1.4, 40, 30, 0]], 151, { noise: 5 });
  ok(g.reason === "weak" || g.reason === "none" || g.conf < S.GLOW.minConf, `too dim to be real: not trusted (${g.reason}, ${g.conf.toFixed(2)})`);
  ok(g.hint === "wider", `too dim: the hint says to light more pixels (${g.hint})`);
}

// ---- exposure / white balance / ambient drift ----
{
  const lamp = [[300, 160, 70, 190, 120, 0], [700, 400, 40, 160, 90, 0]]; // static broad light, strongly structured
  const base = { bg: lamp, noise: 4 };
  // lit frame 25% brighter overall (auto exposure): without the fit, 25% of the lamp (about 17) would be a big false glow
  const g = glowOf([[520, 300, 20, 26, 20, 0]], 161, { ...base, gain: 1 }, {});
  const lit = (gain) => {
    const rand = rng(162), dark = mkdark(rand, { ...base, gain: 1 }), l = S.downscaleLuma(frame([[520, 300, 20, 26, 20, 0]], rand, { ...base, gain }), W, H);
    return S.findGlow(dark.data, l.data, l.w, l.h, { f: l.f });
  };
  ok(!g.reason && dist(g, 520, 300) < 4, "drift: no gain change, patch found");
  for (const gain of [1.25, 0.8]) {
    const r = lit(gain);
    ok(!r.reason && dist(r, 520, 300) < 6 && r.conf >= S.GLOW.minConf, `drift: exposure x${gain} between dark and lit: patch found ${r.x ? dist(r, 520, 300).toFixed(1) : "-"} px off (conf ${r.conf?.toFixed(2)}, fitted gain ${r.gain?.toFixed(2)})`);
  }
  // an ambient ramp (daylight / a lamp warming): dark frames before and after bracket the lit one
  const rampTest = (useAfter) => {
    const rand = rng(163), d0 = mkdark(rand, { ...base, gain: 1, ramp: 0 }), l = S.downscaleLuma(frame([[520, 300, 8, 30, 22, 0]], rand, { ...base, gain: 1, ramp: 0 }), W, H);
    // ambient grows from left to right between the frames: add a ramp image
    const add = (img, amt) => { for (let y = 0; y < img.h; y++) for (let x = 0; x < img.w; x++) img.data[y * img.w + x] += amt * (x / img.w); };
    add(l, 5); // by lit time +5 (right edge)
    const d1 = { ...d0, data: Float32Array.from(d0.data) };
    add(d1, 10);
    return S.findGlow(d0.data, l.data, l.w, l.h, { f: l.f, ...(useAfter ? { darkAfter: d1.data } : {}) });
  };
  const r0 = rampTest(false), r1 = rampTest(true);
  ok(!r1.reason && dist(r1, 520, 300) < 8, `ambient ramp: with a dark frame after as well, patch found (${r1.x ? dist(r1, 520, 300).toFixed(1) : "-"} px off)`);
  ok(!r0.reason ? dist(r0, 520, 300) < 20 : true, `ambient ramp: even without it nothing absurd happens (${r0.reason || dist(r0, 520, 300).toFixed(0) + " px off"})`);
}

// ---- saturation ----
{
  const g = glowOf([[480, 270, 1000, 45, 40, 0]], 171);
  ok(!g.reason && g.saturated > 0.02 && g.hint === "narrower", `saturated patch: reported (${(g.saturated * 100).toFixed(1)}% of pixels), hint ${g.hint}`);
  ok(dist(g, 480, 270) < 6, `saturated patch: centre still right (${dist(g, 480, 270).toFixed(1)} px)`);
  const clean = glowOf([[480, 270, 40, 45, 40, 0]], 172);
  ok(clean.saturated === 0 && clean.hint === null && g.conf <= clean.conf, `unsaturated patch: nothing flagged (conf ${clean.conf.toFixed(2)} vs ${g.conf.toFixed(2)})`);
}

// ---- overlapping neighbours: big patches, spots only ~30 px apart ----
{
  const rand = rng(181), n = 192, plan = S.glowPlan(n, { anchors: 24 });
  const truth = Array.from({ length: n }, (_, i) => [90 + 780 * (i / (n - 1)), 300 + 30 * Math.sin(i / 25)]);
  const dark = mkdark(rand, {});
  const found = [];
  for (const a of plan.anchors) {
    const [mx, my] = truth[Math.round(a.c)];
    const lit = S.downscaleLuma(frame([[mx, my, 20, 55, 35, 0.1]], rand, {}), W, H);
    const g = S.findGlow(dark.data, lit.data, lit.w, lit.h, { f: lit.f });
    found.push({ c: a.c, x: g.x, y: g.y, conf: g.conf, reason: g.reason });
  }
  const sol = S.solveGlow(n, found);
  let worst = 0;
  for (let i = 0; i < n; i++) if (!sol.est[i]) worst = Math.max(worst, Math.hypot(sol.x[i] - truth[i][0], sol.y[i] - truth[i][1]));
  ok(sol.found >= 22 && worst < 10, `overlapping glows (spots ${(780 / 23).toFixed(0)} px apart, glow 55 px): ${sol.found}/24 used, worst ${worst.toFixed(1)} px`);
}

// ---- adapting the spot width ----
{
  ok(S.glowAdaptWidth(4, "wider") > 4 && S.glowAdaptWidth(4, "wider") <= 12, `glowAdaptWidth: wider (${S.glowAdaptWidth(4, "wider")})`);
  ok(S.glowAdaptWidth(8, "narrower") < 8 && S.glowAdaptWidth(8, "narrower") >= S.GLOW.minWidth, `glowAdaptWidth: narrower (${S.glowAdaptWidth(8, "narrower")})`);
  ok(S.glowAdaptWidth(5, null) === 5 && S.glowAdaptWidth(S.GLOW.minWidth, "narrower") === S.GLOW.minWidth, "glowAdaptWidth: no hint = no change; never below the minimum");
  const k = S.glowPlan(904);
  ok(k.width <= 6 && k.anchors.length <= 24, `default plan for the kitchen strip: ${k.anchors.length} spots of ${k.width} px`);
}


// ======================================================================================================================
// Colour glow scan: up to three stretches glow at once (red, green, blue) and are told apart by colour.
// ======================================================================================================================
const RESP = [[1, 0.12, 0.06], [0.14, 1, 0.12], [0.05, 0.2, 1]]; // how the surfaces + camera show red / green / blue light

// one RGB camera frame (Uint8, 3 per pixel). puddles: [[x, y, amp, sx, sy, colour index]]. wb = camera white balance.
function frameRGB(puddles, rand, o = {}) {
  const img = new Float32Array(W * H * 3);
  const add = (cx, cy, amp, sx, sy, vec) => {
    const Rx = Math.ceil(sx * 3.2), Ry = Math.ceil(sy * 3.2);
    for (let y = Math.max(0, Math.floor(cy) - Ry); y <= Math.min(H - 1, Math.floor(cy) + Ry); y++) {
      for (let x = Math.max(0, Math.floor(cx) - Rx); x <= Math.min(W - 1, Math.floor(cx) + Rx); x++) {
        const g = amp * Math.exp(-((x - cx) ** 2 / (2 * sx * sx) + (y - cy) ** 2 / (2 * sy * sy))), q = (y * W + x) * 3;
        // the surface is a little different colour from place to place
        const tint = 1 + 0.12 * Math.sin(x / 140 + y / 90);
        img[q] += g * vec[0] * tint;
        img[q + 1] += g * vec[1];
        img[q + 2] += g * vec[2] / tint;
      }
    }
  };
  add(W * 0.93, H * 0.5, 80, 2, 2, [1, 1, 1]);
  add(W * 0.06, H * 0.9, 60, 3, 3, [1, 0.8, 0.5]);
  for (const [x, y, amp, sx, sy, ci] of puddles) add(x, y, amp, sx, sy, RESP[ci]);
  const wb = o.wb ?? [1.3, 1, 0.65], gain = 1 + (o.drift ?? 0.1) * (rand() - 0.5);
  const out = new Uint8ClampedArray(W * H * 3);
  for (let p = 0, q = 0; p < W * H; p++, q += 3) for (let c = 0; c < 3; c++) out[q + c] = Math.round(10 + gain * wb[c] * img[q + c] + gauss(rand) * (o.noise ?? 4));
  return S.downscaleRGB(out, W, H);
}

// the whole colour glow scan of a run of n pixels. Returns the truth next to the solution and the numbers of pictures.
function scanColour(n, seed, o = {}) {
  const rand = rng(seed);
  const plan = S.glowPlan(n, o.plan);
  const truth = Array.from({ length: n }, (_, i) => path(i / (n - 1)));
  const items = S.glowScheduleColour([{ key: "a", first: 0, plan }]);
  const centre = (a) => {
    let mx = 0, my = 0;
    for (let k = 0; k < a.width; k++) {
      mx += truth[a.start + k][0];
      my += truth[a.start + k][1];
    }
    return [mx / a.width, my / a.width];
  };
  const puddleOf = (a, ci) => {
    const [mx, my] = centre(a);
    const size = 14 + 22 * rand(), amp = (o.dim ? 6 : 22) + (o.dim ? 5 : 50) * rand();
    return [mx, my, amp, size * 1.3, size, ci];
  };
  const bad = new Set(o.occluded || []);
  const darkAt = new Map(), pics = [];
  let model = null;
  const cal = [];
  const found = plan.anchors.map((a) => ({ c: a.c, x: null, y: null, conf: 0, a }));
  items.forEach((it, idx) => {
    if (it.kind === "dark") {
      darkAt.set(idx, frameRGB([], rand, o));
      pics.push(darkAt.get(idx));
    } else if (it.kind === "calib") {
      // the whole range lit: a puddle every ~60 pixels of the picture along the path
      const ps = Array.from({ length: 14 }, (_, k) => {
        const [x, y] = path(k / 13);
        return [x, y, 30, 40, 32, it.ci];
      });
      cal.push(frameRGB(ps, rand, o));
      pics.push(cal.at(-1));
      if (cal.length === 3) model = S.glowColourModel(darkAt.get(0), cal);
    } else {
      const ps = it.segs.map((sg) => (bad.has(sg.a) ? [...centre(plan.anchors[sg.a]), 1.0, 20, 20, sg.ci] : puddleOf(plan.anchors[sg.a], sg.ci)));
      if (o.speck) ps.push([40 + 800 * rand(), 40 + 400 * rand(), 255, 1.2, 1.2, 0]);
      const lit = frameRGB(ps, rand, o);
      pics.push(lit);
      const r = S.findGlowsColour(darkAt.get(it.dark), lit, model, { f: lit.f, want: it.segs.map((q) => q.ci) });
      for (const sg of it.segs) {
        const g = r.glows[sg.ci];
        found[sg.a] = { c: plan.anchors[sg.a].c, x: g.x, y: g.y, conf: g.conf, reason: g.reason, a: plan.anchors[sg.a] };
      }
    }
  });
  const sol = S.solveGlow(n, found);
  return { n, plan, truth, found, sol, items, model };
}

// ---- the colour schedule ----
{
  const p = S.glowPlan(279), q = S.glowPlan(145);
  const sch = S.glowScheduleColour([{ key: "a", first: 0, plan: p }, { key: "b", first: 279, plan: q }]);
  ok(sch[0].kind === "dark" && sch.slice(1, 4).map((x) => `${x.kind}${x.ci}`).join() === "calib0,calib1,calib2", "colour schedule: dark, then the whole range lit red, green, blue");
  ok(sch.slice(1, 4).every((x) => x.start === 0 && x.count === 424), "colour schedule: calibration lights the whole scanned range");
  const segs = sch.filter((x) => x.kind === "segs");
  const seen = new Map();
  for (const it of segs) for (const sg of it.segs) seen.set(`${sg.key}:${sg.a}`, (seen.get(`${sg.key}:${sg.a}`) || 0) + 1);
  ok(seen.size === p.anchors.length + q.anchors.length && [...seen.values()].every((v) => v === 1), "colour schedule: every anchor is lit exactly once");
  ok(segs.every((it) => it.segs.length >= 1 && it.segs.length <= 3 && new Set(it.segs.map((x) => x.ci)).size === it.segs.length), "colour schedule: at most 3 stretches per picture, each its own colour");
  ok(segs.every((it) => it.segs.every((x, k) => x.key === it.segs[0].key && (k === 0 || x.a - it.segs[k - 1].a >= 3))), "colour schedule: the three stretches of a picture are well apart in the run");
  const white = S.glowSchedule([{ key: "a", first: 0, plan: p }, { key: "b", first: 279, plan: q }]);
  const colourPics = sch.length, whitePics = white.length;
  ok(colourPics < whitePics * 0.5, `colour schedule: ${colourPics} pictures against ${whitePics} one-at-a-time`);
  ok(segs.every((it) => sch[it.dark].kind === "dark" && it.dark < sch.indexOf(it)), "colour schedule: each picture points at an earlier dark reference");
  ok(segs.every((it) => it.segs.every((x) => x.start >= (x.key === "a" ? 0 : 279) && x.start + x.count <= (x.key === "a" ? 279 : 424))), "colour schedule: stretches are pixels of the light");
  const one = S.glowScheduleColour([{ key: "a", first: 0, plan: S.glowPlan(3) }]);
  ok(one.filter((x) => x.kind === "segs").length >= 1, "colour schedule: a tiny run still works");
}

// ---- the model and the split of one picture ----
{
  const rand = rng(71);
  const dark = frameRGB([], rand), cal = [0, 1, 2].map((ci) => frameRGB([[300, 250, 40, 60, 40, ci], [650, 300, 40, 50, 40, ci]], rand));
  const m = S.glowColourModel(dark, cal);
  ok(m.ok && m.sep > 0.4, `model: red, green and blue look clearly different (separation ${m.sep.toFixed(2)})`);
  const same = S.glowColourModel(dark, [cal[0], cal[0], cal[0]]);
  ok(!same.ok, "model: three identical colours are not usable");
  const lit = frameRGB([[200, 150, 30, 25, 20, 0], [480, 300, 30, 25, 20, 1], [760, 400, 30, 25, 20, 2]], rand);
  const r = S.findGlowsColour(dark, lit, m, { f: lit.f });
  const want = [[200, 150], [480, 300], [760, 400]];
  const errs = r.glows.map((g, c) => (g.x == null ? 1e9 : Math.hypot(g.x - want[c][0], g.y - want[c][1])));
  ok(errs.every((e) => e < 4), `three colours at once are each found in their own place (errors ${errs.map((e) => e.toFixed(1)).join(", ")} px)`);
  // two glows of different colours overlapping a little are still told apart
  const lit2 = frameRGB([[400, 250, 30, 30, 22, 0], [470, 270, 30, 30, 22, 1]], rand);
  const r2 = S.findGlowsColour(dark, lit2, m, { f: lit2.f, want: [0, 1] });
  ok(r2.glows[0].x < r2.glows[1].x - 25 && Math.abs(r2.glows[0].x - 400) < 14 && Math.abs(r2.glows[1].x - 470) < 14, `overlapping red and green glows are separated (${r2.glows[0].x?.toFixed(0)}, ${r2.glows[1].x?.toFixed(0)})`);
  // asking for a colour that is not lit finds nothing
  const r3 = S.findGlowsColour(dark, frameRGB([[300, 250, 30, 25, 20, 0]], rand), m, { f: 1, want: [1, 2] });
  ok(r3.glows[1].x === null && r3.glows[2].x === null, "a colour that is not lit finds no glow");
}

// ---- whole runs, three anchors per picture ----
for (const [n, seed, wb] of [[279, 1, [1.3, 1, 0.65]], [145, 2, [1.7, 1, 0.4]], [192, 3, [0.8, 1, 1.4]], [288, 4, [1.3, 1, 0.65]]]) {
  const r = scanColour(n, seed, { wb });
  const e = errors(r);
  const okAnchors = r.sol.anchors.filter((a) => a.status === "ok").length;
  ok(r.model.ok, `colour run ${n}: the camera tells red, green and blue apart (separation ${r.model.sep.toFixed(2)})`);
  ok(okAnchors >= r.plan.anchors.length - 1, `colour run ${n}: anchors found ${okAnchors}/${r.plan.anchors.length} from ${r.items.length} pictures`);
  ok(e.max < 8 && e.mean < 3.5, `colour run ${n}: pixel places within ${e.max.toFixed(1)} px of the truth (mean ${e.mean.toFixed(2)})`);
}

// ---- colour: dim, noisy, glints, occluded ----
{
  const r = scanColour(192, 21, { noise: 5, dim: true, speck: true, drift: 0.2 });
  const e = errors(r);
  const okA = r.sol.anchors.filter((a) => a.status === "ok").length;
  ok(okA >= r.plan.anchors.length - 2 && e.max < 16, `colour dim, noisy, with glints: ${okA}/${r.plan.anchors.length} anchors, worst pixel ${e.max.toFixed(1)} px off, mean ${e.mean.toFixed(1)}`);
  const occ = [4, 5, 11];
  const q = scanColour(279, 31, { occluded: occ });
  const st = q.sol.anchors.map((a) => a.status);
  ok(occ.every((k) => st[k] !== "ok") && st.filter((x) => x === "ok").length >= q.plan.anchors.length - occ.length - 1, `colour: occluded anchors ${occ} are flagged (${occ.map((k) => st[k]).join(", ")}) and the rest are used`);
  ok(errors(q).max < 14, `colour: pixels near found anchors stay accurate (${errors(q).max.toFixed(1)} px)`);
}

// ---- several angles: a run placed in 3D between its triangulated spots ----
{
  const n = 100, known = [10, 30, 50, 70, 90].map((c) => ({ c, p: [c / 100, 0.2, c < 50 ? 0 : (c - 50) / 100] }));
  const r = S.glowRun3D(n, known);
  ok(r && Math.abs(r.pos[3 * 20] - 0.2) < 1e-6 && Math.abs(r.pos[3 * 60 + 2] - 0.1) < 1e-6, "glowRun3D: pixels between spots lie on the line between them");
  ok(Math.abs(r.pos[0] - 0) < 1e-6 && Math.abs(r.pos[3 * 99] - 0.99) < 1e-6, "glowRun3D: straight on beyond the first and last spot");
  ok(r.est[50] === 0 && r.est[0] === 0 && r.found === 5, "glowRun3D: pixels near a spot count as found");
  const far = S.glowRun3D(100, [40, 50, 60].map((c) => ({ c, p: [c / 100, 0, 0] })));
  ok(far.est[0] === 1 && far.est[99] === 1 && far.est[45] === 0, "glowRun3D: pixels far from any spot are flagged as guessed");
  ok(S.glowRun3D(50, [{ c: 3, p: [0, 0, 0] }]) === null && S.glowRun3D(50, [{ c: 3, p: [0, NaN, 0] }, { c: 9, p: [1, 1, 1] }]) === null, "glowRun3D: needs two good spots");
}

console.log(fails ? `\n${fails} FAILED` : "\nall passed");
process.exit(fails ? 1 : 0);
