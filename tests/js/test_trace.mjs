// Tests for the Trace and edit editor's pure parts (custom_components/light_show_studio/frontend/light-show-studio-map3d.js):
// Geo.spread with breaks, the save/load round trip of breaks (Geo.encodeBreaks / decodeBreaks, via RunEditor._outPts and
// _load), and move / rotate / reverse / delete keeping break flags. RunEditor methods are called on a bare object (no DOM).
// Run:  node tests/js/test_trace.mjs      (exits non-zero on failure)
import { Geo, RunEditor, VIEW_AXES } from "../../custom_components/light_show_studio/frontend/light-show-studio-map3d.js";

let fails = 0;
const ok = (cond, msg) => {
  console.log(`${cond ? "ok  " : "FAIL"} ${msg}`);
  if (!cond) fails++;
};
const near = (a, b, e = 1e-9) => Math.abs(a - b) <= e;
const pt = (P, i) => [P[i * 3], P[i * 3 + 1], P[i * 3 + 2]];
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const E = RunEditor.prototype;
const fake = (extra = {}) => Object.assign(Object.create(E), extra);

// ---- Geo.spread ------------------------------------------------------------------------------------------------------
{
  const L = [[0, 0, 0], [0.4, 0, 0]];
  const S = Geo.spread(L, 5);
  ok(near(S[0], 0) && near(S[12], 0.4) && [0, 1, 2, 3, 4].every((i) => near(S[i * 3], 0.1 * i)), "no break: even spacing, exact ends");
  const m = Geo.spread([[0, 0, 0], [0.2, 0, 0], [0.2, 0.2, 0]], 5);
  ok(near(m[6], 0.2) && near(m[7], 0) && near(m[12], 0.2) && near(m[13], 0.2), "no break: a corner lands on its pixel");
}
{
  // two stretches of 0.2 either side of a 0.6 gap: A(0,0,0) B(.2,0,0) | C(.8,0,0) D(1,0,0)
  const C = [[0, 0, 0], [0.2, 0, 0], [0.8, 0, 0, 1], [1, 0, 0]];
  ok(near(Geo.length(C), 1.0), "Geo.length still counts the gap (it is the drawn length)");
  const S = Geo.spread(C, 9);
  const xs = Array.from({ length: 9 }, (_, i) => S[i * 3]);
  ok(near(xs[0], 0) && near(xs[8], 1), "break: end points exact");
  ok(xs.every((x) => x < 0.2 + 1e-9 || x > 0.8 - 1e-9), "break: nothing placed inside the gap");
  const step = 0.4 / 8;
  ok(near(xs[1] - xs[0], step) && near(xs[2] - xs[1], step) && near(xs[7] - xs[6], step), "break: spacing is by LED length only (gap takes none)");
  // 9 pixels, total 0.4: pixels 4 (d=.2) sits at B, 5 at C
  ok(near(xs[4], 0.2) && near(xs[5], 0.85), "break: pixels either side of the gap are at the stretch ends");
}
{
  const C = [[0, 0, 0], [0.2, 0, 0], [0.8, 0, 0, 1], [1, 0, 0]];
  const one = Geo.spread(C, 1);
  ok(one[0] >= 0 && one[0] <= 0.2 + 1e-9 || one[0] >= 0.8 - 1e-9, "break: n = 1 lands on a stretch, not in the gap");
  const two = Geo.spread(C, 2);
  ok(near(two[0], 0) && near(two[3], 1), "break: n = 2 puts one pixel on each end");
  ok(Geo.spread(C, 0).length === 0, "n = 0 gives nothing");
  const l = Geo.spread([[0, 0, 0], [1, 0, 0]], 2);
  ok(near(l[0], 0) && near(l[3], 1), "no break: n = 2 is the two ends");
  ok(near(Geo.spread([[0, 0, 0], [1, 0, 0]], 1)[0], 0.5), "no break: n = 1 is the middle");
}
{
  // two breaks in a row of stretches, the second stretch very short
  const C = [[0, 0, 0], [0.1, 0, 0], [0.5, 0, 0, 1], [0.6, 0, 0], [0.9, 0, 0, 1], [1, 0, 0]];
  const S = Geo.spread(C, 13); // total 0.1 + 0.1 + 0.1 = 0.3
  const xs = Array.from({ length: 13 }, (_, i) => S[i * 3]);
  ok(xs.every((x) => (x <= 0.1 + 1e-9) || (x >= 0.5 - 1e-9 && x <= 0.6 + 1e-9) || x >= 0.9 - 1e-9), "two breaks: nothing inside either gap");
  ok(near(xs[0], 0) && near(xs[12], 1), "two breaks: exact ends");
  ok(xs.every((x, i) => i === 0 || x >= xs[i - 1] - 1e-12), "two breaks: pixels stay in order");
  ok(xs.slice(1).every((x, i) => near(Math.abs(x - xs[i]), 0.025) || Math.abs(x - xs[i]) > 0.3), "two breaks: constant step along the LED stretches");
}
{
  // break at the last corner: the trailing stretch has no LEDs, the run ends at the corner before it
  const C = [[0, 0, 0], [0.5, 0, 0], [0.9, 0.9, 0, 1]];
  const S = Geo.spread(C, 6);
  ok(near(S[15], 0.5) && near(S[16], 0) && near(S[0], 0), "break at last corner: run ends at the corner before the break");
  ok(near(Geo.spread(C, 6)[6], 0.2), "break at last corner: spacing unaffected");
  // a break at the second corner: the first stretch has no LEDs, the run starts at corner 1
  const D = [[0, 0, 0], [0.5, 0.5, 0, 1], [0.9, 0.5, 0]];
  const T = Geo.spread(D, 5);
  ok(near(T[0], 0.5) && near(T[1], 0.5) && near(T[12], 0.9), "break right after the first corner: run starts at the corner after the break");
}
{
  // everything is a break / one corner: collapses to a point without NaN
  const S = Geo.spread([[0.3, 0.3, 0.3]], 4);
  ok(Array.from(S).every((v) => near(v, 0.3)), "one corner: all pixels there");
  const T = Geo.spread([[0, 0, 0], [1, 0, 0, 1]], 3);
  ok(Array.from(T).every((v) => Number.isFinite(v)), "all-break run gives finite positions");
}

// ---- Geo.encodeBreaks / decodeBreaks and the editor's _outPts / _load ------------------------------------------------
const flags = (cs) => cs.map((c) => (c[3] ? 1 : 0));
{
  const cs = [[0.1, 0.2, 0.3], [0.2, 0.2, 0.3], [0.6, 0.2, 0.3, 1], [0.8, 0.2, 0.5]];
  const enc = Geo.encodeBreaks(cs);
  ok(eq(enc, [[0.1, 0.2, 0.3], [0.2, 0.2, 0.3], [0.2, 0.2, 0.3], [0.6, 0.2, 0.3], [0.6, 0.2, 0.3], [0.8, 0.2, 0.5]]), "encode: a break becomes [.. A, A, B, B ..]");
  const dec = Geo.decodeBreaks(enc);
  ok(dec.length === 4 && eq(flags(dec), [0, 0, 1, 0]) && eq(dec.map((c) => c.slice(0, 3)), cs.map((c) => c.slice(0, 3))), "decode: back to 4 corners with the flag on the third");
  ok(eq(Geo.encodeBreaks(Geo.decodeBreaks(Geo.encodeBreaks(cs))), enc), "encode(decode(encode)) is stable");
  ok(eq(Geo.encodeBreaks([[0, 0, 0], [1, 1, 1]]), [[0, 0, 0], [1, 1, 1]]), "no breaks: encoded unchanged");
  ok(eq(Geo.encodeBreaks([[0, 0, 0, 1], [1, 1, 1]]).length, 2), "a flag on the first corner is ignored");
}
{
  // consecutive breaks, a break whose corners coincide, a break at the last corner
  const cases = {
    "two breaks in a row": [[0, 0, 0], [0.1, 0, 0], [0.4, 0, 0, 1], [0.5, 0, 0], [0.8, 0, 0, 1], [0.9, 0, 0]],
    "break at last corner": [[0, 0, 0], [0.3, 0, 0], [0.7, 0.7, 0.2, 1]],
    "break on the second corner": [[0, 0, 0], [0.5, 0.5, 0.5, 1], [0.9, 0.5, 0.5]],
    "breaks on consecutive corners": [[0, 0, 0], [0.2, 0, 0], [0.4, 0, 0, 1], [0.6, 0, 0, 1], [0.8, 0, 0]],
    "a break to the same spot": [[0, 0, 0], [0.5, 0, 0], [0.5, 0, 0, 1], [0.9, 0, 0]],
  };
  for (const [name, cs] of Object.entries(cases)) {
    const dec = Geo.decodeBreaks(Geo.encodeBreaks(cs));
    ok(dec.length === cs.length && eq(flags(dec), flags(cs)) && eq(dec.map((c) => c.slice(0, 3)), cs.map((c) => c.slice(0, 3))), `round trip: ${name}`);
  }
}
{
  // duplicate corners that are not a break stay as they are
  const A = [0.1, 0.1, 0.1], B = [0.5, 0.1, 0.1], C = [0.9, 0.1, 0.1];
  ok(eq(flags(Geo.decodeBreaks([A, A, B, C])), [0, 0, 0, 0]) && Geo.decodeBreaks([A, A, B, C]).length === 4, "duplicate corners [A, A, B, C] are not a break");
  ok(Geo.decodeBreaks([A, B, B, C]).length === 4, "duplicate corners [A, B, B, C] are not a break");
  ok(Geo.decodeBreaks([A, A, A]).length === 3, "[A, A, A] is not a break");
  ok(Geo.decodeBreaks([]).length === 0 && Geo.decodeBreaks([A]).length === 1, "empty and single point lists decode");
  ok(eq(flags(Geo.decodeBreaks([A, A, B, B])), [0, 1]), "[A, A, B, B] is a break");
}
{
  // through the editor: _outPts (with rounding / clamping) then _load
  const ed = fake();
  const cs = [[0.1, 0.5, 0.5], [0.3, 0.5, 0.5], [0.7, 0.5, 0.5, 1], [0.9, 0.5, 0.5]];
  const n = 20;
  const run = { corners: cs, count: n, P: Geo.spread(cs, n) };
  const pts = ed._outPts(run);
  ok(pts.length === 6, "_outPts writes the break as repeated corners");
  const raw = { id: "r", count: n, pts, positions: Array.from(run.P, (v) => Math.round(v * 1000)) };
  const r = ed._load(raw, [1, 2, 3]);
  ok(r.kind === "traced" && r.corners.length === 4 && eq(flags(r.corners), [0, 0, 1, 0]), "_load: a saved scan with breaks is traced again, flags restored");
  ok(ed._key(r) === r.orig && !ed._changed(r), "_load: loaded run is not 'changed'");
  ok(eq(ed._outPts(r), pts), "_outPts after load equals what was saved");
  // positions-less (a fresh trace never saved to positions) loads by spreading
  const r2 = ed._load({ id: "r2", count: n, pts }, [1, 2, 3]);
  ok(r2.kind === "traced" && eq(flags(r2.corners), [0, 0, 1, 0]) && near(r2.P[3 * 8], run.P[3 * 8], 1e-9), "_load without positions: spread honours the break");
  const gap = Array.from({ length: n }, (_, i) => r2.P[i * 3]).filter((x) => x > 0.3 + 1e-9 && x < 0.7 - 1e-9);
  ok(gap.length === 0, "_load: no pixel inside the break");
  // a saved scan whose positions do not match the pts keeps the scan (no mis-decode)
  const sc = ed._load({ id: "r3", count: n, pts, positions: Array.from({ length: n * 3 }, () => 500) }, [1, 2, 3]);
  ok(sc.kind === "scan" && sc.corners === null, "_load: positions that differ from the trace stay a scan");
  // clamping: out of range corners are clamped on save, flag kept
  const o = ed._outPts({ corners: [[-1, 0.5, 0.5], [2, 0.5, 0.5, 1], [2, 0.5, 0.5]], count: 3, P: new Float64Array(9) });
  ok(o.length === 5 && o[0][0] === 0 && eq(o[0], o[1]) && o[2][0] === 1 && eq(o[2], o[3]), "_outPts clamps and still writes the break");
  // an old run with only two end pts and no break loads as before
  const old = ed._load({ id: "o", count: 4, pts: [[0, 0.5, 0.5], [1, 0.5, 0.5]] }, [1, 2, 3]);
  ok(old.corners.length === 2 && old.fresh, "_load: plain two-point run unchanged");
}

// ---- move / rotate / reverse / delete keep the flags -----------------------------------------------------------------
const mk = () => [[0.2, 0.3, 0.4], [0.3, 0.3, 0.4], [0.6, 0.3, 0.4, 1], [0.7, 0.3, 0.5]];
{
  // whole-run move
  const cs = mk(), r = { count: 20, P: Geo.spread(cs, 20), corners: cs };
  const ed = fake({ cur: r, _syncDots() {} });
  const base = new Float64Array(r.P);
  ed._shiftBy({ base, a: 0, b: 19, cb: cs.map((c) => [...c]) }, [0.1, 0.05, 0]);
  ok(eq(flags(r.corners), [0, 0, 1, 0]), "move: break flags kept");
  ok(r.corners.every((c) => c.slice(0, 3).every(Number.isFinite)) && r.corners.every((c) => c.length === 3 || c.length === 4 && c[3] === 1), "move: no NaN, flag is exactly 1");
  ok(near(r.corners[0][0], 0.3) && near(r.corners[3][1], 0.35), "move: corners moved");
  const S = Geo.spread(r.corners, 20);
  let d = 0;
  for (let i = 0; i < S.length; i++) d = Math.max(d, Math.abs(S[i] - r.P[i]));
  ok(d < 1e-9, "move: pixels still match the spread of the moved corners");
}
{
  // rotate drag (whole run)
  const cs = mk(), r = { count: 20, P: Geo.spread(cs, 20), corners: cs };
  const ax = VIEW_AXES.top;
  const ed = fake({ cur: r, _syncDots() {}, _ax: () => ax });
  const s = { k: "rot", base: new Float64Array(r.P), cb: cs.map((c) => [...c]), a: 0, b: 19, pivot: [0.45, 0.3, 0.45], a0: 0, cx: 0, cy: 0 };
  ed._mv(10, 10, null, s); // atan2(10, 10) = 45 degrees
  ok(eq(flags(r.corners), [0, 0, 1, 0]), "rotate: break flags kept");
  ok(near(dist(r.corners[0], [0.45, 0.3, 0.45]), dist(cs[0], [0.45, 0.3, 0.45]), 1e-9), "rotate: corners keep their distance from the pivot");
  const moved = r.corners.some((c, i) => Math.abs(c[0] - cs[i][0]) > 1e-3 || Math.abs(c[2] - cs[i][2]) > 1e-3);
  ok(moved, "rotate: corners actually turned");
  // the break gap stays a gap after rotation
  const S = Geo.spread(r.corners, 20);
  ok(Math.max(...Array.from(S, (v, i) => Math.abs(v - r.P[i]))) < 2e-3, "rotate: pixels follow the rotated corners");
}
{
  // dragging one corner (the break one) keeps its flag
  const cs = mk(), r = { count: 20, P: Geo.spread(cs, 20), corners: cs };
  const ed = fake({ cur: r, _syncDots() {}, _ax: () => VIEW_AXES.top, viewer: { unproject: () => [0.65, 0.3, 0.45] }, _snapPos: (p) => p });
  ed._mv(50, 50, null, { k: "corner", i: 2, x: 0, y: 0, moved: false });
  ok(eq(flags(r.corners), [0, 0, 1, 0]) && near(r.corners[2][0], 0.65), "drag corner: moved and still starts after the break");
  const s1 = { k: "corner", i: 1, x: 0, y: 0, moved: false };
  ed._mv(50, 50, null, s1);
  ok(eq(flags(r.corners), [0, 0, 1, 0]), "drag corner: neighbour drag leaves other flags");
}
{
  // whole-run transforms (mirror / turn) go through _applyXf
  const cs = mk(), r = { count: 20, P: Geo.spread(cs, 20), corners: cs, kind: "traced" };
  const ed = fake({ cur: r, _target: () => [0, 19], _syncDots() {}, _commit() {} });
  ed._applyXf((P, a, b) => Geo.rotate(P, a, b, VIEW_AXES.top, [0.5, 0.5, 0.5], Math.PI / 2));
  ok(eq(flags(r.corners), [0, 0, 1, 0]) && r.kind === "traced", "turn: break flags kept");
  const ed2 = fake({ cur: r, _target: () => [0, 9], _syncDots() {}, _commit() {} });
  ed2._applyXf((P, a, b) => Geo.rotate(P, a, b, VIEW_AXES.top, [0.5, 0.5, 0.5], 0.2));
  ok(r.corners === null, "turning part of a run drops the corners (and so the breaks), as before");
}
{
  // flip direction: the break stays between the same two corners
  const cs = mk();
  const rv = Geo.reverseCorners(cs);
  ok(eq(rv.map((c) => c.slice(0, 3)), [...cs].reverse().map((c) => c.slice(0, 3))), "reverse: corners reversed");
  ok(eq(flags(rv), [0, 0, 1, 0]), "reverse: the break (between corners 1 and 2) is still between the same two corners");
  ok(eq(flags(Geo.reverseCorners(rv)), flags(cs)), "reverse twice returns the same breaks");
  const total = (c) => { let t = 0; for (let i = 1; i < c.length; i++) if (!c[i][3]) t += dist(c[i - 1], c[i]); return t; };
  ok(near(total(rv), total(cs)), "reverse: LED length unchanged");
  const edge = Geo.reverseCorners([[0, 0, 0], [0.5, 0, 0], [0.9, 0, 0, 1]]);
  ok(eq(flags(edge), [0, 1, 0]) && !edge[0][3], "reverse: break at the last corner becomes a break after the first");
  const Sa = Geo.spread(cs, 11), Sb = Geo.spread(rv, 11);
  let d = 0;
  for (let i = 0; i < 11; i++) d = Math.max(d, dist(pt(Sa, i), pt(Sb, 10 - i)));
  ok(d < 1e-9, "reverse: pixels land in the same places, in the opposite order");
}
{
  // delete a corner: deleting the first corner must not leave a flag on the new first one
  const cs = [[0, 0, 0], [0.2, 0, 0, 1], [0.5, 0, 0], [0.9, 0, 0]];
  const r = { count: 8, P: new Float64Array(24), corners: cs };
  const ed = fake({ cur: r, cornerSel: 0, _commit() {}, _syncDots() {} });
  ed._deleteCorner();
  ok(r.corners.length === 3 && !r.corners[0][3], "delete first corner: the new first corner has no break flag");
  const cs2 = [[0, 0, 0], [0.2, 0, 0], [0.5, 0, 0, 1], [0.9, 0, 0]];
  const r2 = { count: 8, P: new Float64Array(24), corners: cs2 };
  fake({ cur: r2, cornerSel: 2, _commit() {}, _syncDots() {} })._deleteCorner();
  ok(r2.corners.length === 3 && flags(r2.corners).every((f) => !f), "delete a break corner: the break goes with it");
}

console.log(fails ? `\n${fails} FAILED` : "\nall passed");
process.exit(fails ? 1 : 0);
