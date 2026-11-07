// Light Show Studio: multi-angle 3D scan. Pure functions, no DOM, so they run in the browser and in node (tests/js/test_multiview.mjs).
// Spec: docs/MAPPING_V2.md "Multi-angle scan".
//
// Input: views = [{width, height, points, fovDeg?, orientation?}] where points maps LED index -> [u, v] in camera pixels (y down), as
// decoded by the colour scan, and orientation = {alpha, beta, gamma} (degrees, DeviceOrientationEvent) when the phone gave it.
// All views come from one phone camera, so they share one focal length (starting from fovDeg, default 65 degrees across the longer side).
// Method: relative pose of the two views that share the most LEDs (normalised 8-point essential matrix in RANSAC, or a plane
// homography when the lights are flat), triangulate; add each further view by PnP (DLT, or the plane homography for flat scenes) in
// RANSAC and triangulate what it newly sees; bundle adjustment (Levenberg-Marquardt, Huber loss, Schur complement) over poses, points
// and the shared focal length; drop observations with large reprojection error. Result in the first view's frame, turned so y is up and
// z points towards the first camera, then fitted into the unit box. LEDs seen in fewer than two views are placed along the string order
// (or on the single view's ray at the scene's middle depth) and flagged `est`.

// ---- small linear algebra -------------------------------------------------------------------------------------------------
const R2D = 180 / Math.PI;
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const norm = (a) => Math.hypot(a[0], a[1], a[2]);
const unit = (a) => {
  const n = norm(a) || 1;
  return [a[0] / n, a[1] / n, a[2] / n];
};
// 3x3 matrices are flat row-major arrays of 9
const mm = (A, B) => {
  const C = new Array(9);
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) C[i * 3 + j] = A[i * 3] * B[j] + A[i * 3 + 1] * B[3 + j] + A[i * 3 + 2] * B[6 + j];
  return C;
};
const mt = (A) => [A[0], A[3], A[6], A[1], A[4], A[7], A[2], A[5], A[8]];
const mv = (A, x) => [A[0] * x[0] + A[1] * x[1] + A[2] * x[2], A[3] * x[0] + A[4] * x[1] + A[5] * x[2], A[6] * x[0] + A[7] * x[1] + A[8] * x[2]];
const mtv = (A, x) => [A[0] * x[0] + A[3] * x[1] + A[6] * x[2], A[1] * x[0] + A[4] * x[1] + A[7] * x[2], A[2] * x[0] + A[5] * x[1] + A[8] * x[2]];
const det3 = (A) => A[0] * (A[4] * A[8] - A[5] * A[7]) - A[1] * (A[3] * A[8] - A[5] * A[6]) + A[2] * (A[3] * A[7] - A[4] * A[6]);
const I3 = () => [1, 0, 0, 0, 1, 0, 0, 0, 1];
const cols3 = (a, b, c) => [a[0], b[0], c[0], a[1], b[1], c[1], a[2], b[2], c[2]];

export function rodrigues(w) {
  const th = norm(w);
  if (th < 1e-12) return [1, -w[2], w[1], w[2], 1, -w[0], -w[1], w[0], 1];
  const k = [w[0] / th, w[1] / th, w[2] / th], c = Math.cos(th), s = Math.sin(th), v = 1 - c;
  return [
    c + k[0] * k[0] * v, k[0] * k[1] * v - k[2] * s, k[0] * k[2] * v + k[1] * s,
    k[1] * k[0] * v + k[2] * s, c + k[1] * k[1] * v, k[1] * k[2] * v - k[0] * s,
    k[2] * k[0] * v - k[1] * s, k[2] * k[1] * v + k[0] * s, c + k[2] * k[2] * v,
  ];
}
const angleBetween = (A, B) => {
  const M = mm(mt(A), B);
  return Math.acos(Math.max(-1, Math.min(1, (M[0] + M[4] + M[8] - 1) / 2))) * R2D;
};

// One-sided Jacobi SVD of an m x n matrix (m >= n), rows as arrays. Returns {U (m x n), S (n, descending), V (n x n)}, A = U diag(S) V^T.
export function svd(A0) {
  const m = A0.length, n = A0[0].length;
  const U = A0.map((r) => Float64Array.from(r));
  const V = Array.from({ length: n }, (_, i) => {
    const r = new Float64Array(n);
    r[i] = 1;
    return r;
  });
  for (let sweep = 0; sweep < 60; sweep++) {
    let off = 0;
    for (let p = 0; p < n - 1; p++) {
      for (let q = p + 1; q < n; q++) {
        let a = 0, b = 0, g = 0;
        for (let i = 0; i < m; i++) {
          a += U[i][p] * U[i][p];
          b += U[i][q] * U[i][q];
          g += U[i][p] * U[i][q];
        }
        if (g === 0 || Math.abs(g) <= 1e-15 * Math.sqrt(a * b)) continue;
        off = Math.max(off, Math.abs(g) / Math.sqrt(a * b));
        const zeta = (b - a) / (2 * g), t = (zeta >= 0 ? 1 : -1) / (Math.abs(zeta) + Math.sqrt(1 + zeta * zeta));
        const c = 1 / Math.sqrt(1 + t * t), s = c * t;
        for (let i = 0; i < m; i++) {
          const up = U[i][p], uq = U[i][q];
          U[i][p] = c * up - s * uq;
          U[i][q] = s * up + c * uq;
        }
        for (let i = 0; i < n; i++) {
          const vp = V[i][p], vq = V[i][q];
          V[i][p] = c * vp - s * vq;
          V[i][q] = s * vp + c * vq;
        }
      }
    }
    if (off < 1e-14) break;
  }
  const S = new Float64Array(n);
  for (let j = 0; j < n; j++) {
    let s = 0;
    for (let i = 0; i < m; i++) s += U[i][j] * U[i][j];
    S[j] = Math.sqrt(s);
  }
  const order = [...S.keys()].sort((a, b) => S[b] - S[a]);
  const Uo = U.map((r) => new Float64Array(n)), Vo = V.map((r) => new Float64Array(n)), So = new Float64Array(n);
  order.forEach((j, k) => {
    So[k] = S[j];
    for (let i = 0; i < m; i++) Uo[i][k] = S[j] > 1e-300 ? U[i][j] / S[j] : 0;
    for (let i = 0; i < n; i++) Vo[i][k] = V[i][j];
  });
  return { U: Uo, S: So, V: Vo };
}
const colOf = (M, j) => M.map((r) => r[j]);
const svd3 = (A) => svd([[A[0], A[1], A[2]], [A[3], A[4], A[5]], [A[6], A[7], A[8]]]);
const fromRows3 = (M) => [M[0][0], M[0][1], M[0][2], M[1][0], M[1][1], M[1][2], M[2][0], M[2][1], M[2][2]];

// the unit vector x minimising |A x| (rows given), from the eigenvectors of A^T A
function nullVec(rows, n) {
  const ata = Array.from({ length: n }, () => new Float64Array(n));
  for (const r of rows) for (let i = 0; i < n; i++) if (r[i] !== 0) for (let j = i; j < n; j++) ata[i][j] += r[i] * r[j];
  for (let i = 0; i < n; i++) for (let j = 0; j < i; j++) ata[i][j] = ata[j][i];
  return colOf(svd(ata).V, n - 1);
}

// nearest rotation to M (det +1)
function orthogonalise(M) {
  const { U, V } = svd3(M);
  let R = mm(fromRows3(U), mt(fromRows3(V)));
  if (det3(R) < 0) {
    const U2 = U.map((r) => Float64Array.from(r));
    for (const r of U2) r[2] = -r[2];
    R = mm(fromRows3(U2), mt(fromRows3(V)));
  }
  return R;
}

// Gaussian elimination with partial pivoting; A is an array of Float64Array rows (modified), b a Float64Array (modified). Returns x or null.
function solveLin(A, b) {
  const n = b.length;
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
    if (Math.abs(A[p][c]) < 1e-300) return null;
    if (p !== c) {
      [A[p], A[c]] = [A[c], A[p]];
      [b[p], b[c]] = [b[c], b[p]];
    }
    for (let r = c + 1; r < n; r++) {
      const f = A[r][c] / A[c][c];
      if (f === 0) continue;
      for (let k = c; k < n; k++) A[r][k] -= f * A[c][k];
      b[r] -= f * b[c];
    }
  }
  const x = new Float64Array(n);
  for (let r = n - 1; r >= 0; r--) {
    let s = b[r];
    for (let k = r + 1; k < n; k++) s -= A[r][k] * x[k];
    x[r] = s / A[r][r];
  }
  return x;
}
function inv3(A) {
  const d = det3(A);
  if (Math.abs(d) < 1e-300) return null;
  return [
    (A[4] * A[8] - A[5] * A[7]) / d, (A[2] * A[7] - A[1] * A[8]) / d, (A[1] * A[5] - A[2] * A[4]) / d,
    (A[5] * A[6] - A[3] * A[8]) / d, (A[0] * A[8] - A[2] * A[6]) / d, (A[2] * A[3] - A[0] * A[5]) / d,
    (A[3] * A[7] - A[4] * A[6]) / d, (A[1] * A[6] - A[0] * A[7]) / d, (A[0] * A[4] - A[1] * A[3]) / d,
  ];
}

function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function sample(rand, n, k) {
  const out = new Set();
  while (out.size < k) out.add(Math.floor(rand() * n));
  return [...out];
}
const median = (arr) => {
  if (!arr.length) return 0;
  const s = Float64Array.from(arr).sort();
  return s[s.length >> 1];
};

// ---- device orientation prior -----------------------------------------------------------------------------------------------
// DeviceOrientationEvent (alpha about z, beta about x, gamma about y; degrees) -> rotation taking the phone's own axes (x right, y up the
// screen, z out of it) to the world's (east, north, up): R = Rz(alpha) Rx(beta) Ry(gamma). The back camera looks along -z with image y down,
// so camera axes -> world is R_dev diag(1, -1, -1).
export function deviceRotation(o) {
  const a = ((o.alpha || 0) * Math.PI) / 180, b = ((o.beta || 0) * Math.PI) / 180, g = ((o.gamma || 0) * Math.PI) / 180;
  const Rz = [Math.cos(a), -Math.sin(a), 0, Math.sin(a), Math.cos(a), 0, 0, 0, 1];
  const Rx = [1, 0, 0, 0, Math.cos(b), -Math.sin(b), 0, Math.sin(b), Math.cos(b)];
  const Ry = [Math.cos(g), 0, Math.sin(g), 0, 1, 0, -Math.sin(g), 0, Math.cos(g)];
  return mm(mm(Rz, Rx), Ry);
}
// camera -> world rotation of a view with orientation
export const cameraToWorld = (o) => mm(deviceRotation(o), [1, 0, 0, 0, -1, 0, 0, 0, -1]);

// ---- views -------------------------------------------------------------------------------------------------------------------
// Pixels are turned into units of the longer side of the picture (u' = (u - w/2) / m), so one focal length F (in those units) serves
// views of any size from the same camera. A bearing is (u'/F, v'/F, 1).
export const fovToF = (fovDeg) => 0.5 / Math.tan((fovDeg * Math.PI) / 360);
export const fToFov = (F) => (2 * Math.atan(0.5 / F) * 180) / Math.PI;

function prepView(v, n) {
  const m = Math.max(v.width, v.height);
  const pts = new Map();
  const entries = v.points instanceof Map ? v.points.entries() : Object.entries(v.points || {});
  for (const [k, p] of entries) {
    const i = Number(k);
    if (!Number.isInteger(i) || i < 0 || i >= n || !p || !Number.isFinite(p[0]) || !Number.isFinite(p[1])) continue;
    pts.set(i, [(p[0] - v.width / 2) / m, (p[1] - v.height / 2) / m]);
  }
  return { m, w: v.width, h: v.height, pts, fovDeg: v.fovDeg, Rcw: v.orientation ? cameraToWorld(v.orientation) : null };
}

// how many views see each LED: Uint8Array(n); coverage stats for the wizard
export function coverage(viewsIn, n) {
  const seen = new Uint8Array(n);
  for (const v of viewsIn) {
    const keys = v.points instanceof Map ? v.points.keys() : Object.keys(v.points || {});
    for (const k of keys) {
      const i = Number(k);
      if (i >= 0 && i < n) seen[i]++;
    }
  }
  let one = 0, many = 0;
  for (let i = 0; i < n; i++) {
    if (seen[i] >= 1) one++;
    if (seen[i] >= 2) many++;
  }
  return { seen, any: one, multi: many, n };
}

// ---- geometry --------------------------------------------------------------------------------------------------------------------
const bearing = (p, F) => [p[0] / F, p[1] / F, 1];

// camera centre and ray direction (world) for a bearing seen by a camera with world->camera (R, t)
function ray(R, t, b) {
  return { c: mtv(R, [-t[0], -t[1], -t[2]]), d: unit(mtv(R, b)) };
}

// least-squares point nearest to all rays; returns {X, ok, maxAngle (deg), depths}
function triangulate(rays) {
  const A = [0, 0, 0, 0, 0, 0, 0, 0, 0], b = [0, 0, 0];
  for (const { c, d } of rays) {
    const P = [1 - d[0] * d[0], -d[0] * d[1], -d[0] * d[2], -d[1] * d[0], 1 - d[1] * d[1], -d[1] * d[2], -d[2] * d[0], -d[2] * d[1], 1 - d[2] * d[2]];
    for (let i = 0; i < 9; i++) A[i] += P[i];
    const pc = mv(P, c);
    b[0] += pc[0];
    b[1] += pc[1];
    b[2] += pc[2];
  }
  const Ai = inv3(A);
  if (!Ai) return { X: null, ok: false, maxAngle: 0 };
  const X = mv(Ai, b);
  let maxAngle = 0, front = true;
  for (let i = 0; i < rays.length; i++) {
    if (dot(sub(X, rays[i].c), rays[i].d) <= 0) front = false;
    for (let j = i + 1; j < rays.length; j++) maxAngle = Math.max(maxAngle, Math.acos(Math.max(-1, Math.min(1, dot(rays[i].d, rays[j].d)))) * R2D);
  }
  return { X, ok: front, maxAngle };
}

function project(R, t, F, X) {
  const x = R[0] * X[0] + R[1] * X[1] + R[2] * X[2] + t[0], y = R[3] * X[0] + R[4] * X[1] + R[5] * X[2] + t[1], z = R[6] * X[0] + R[7] * X[1] + R[8] * X[2] + t[2];
  if (z < 1e-9) return null;
  return [(F * x) / z, (F * y) / z];
}

// ---- two views ---------------------------------------------------------------------------------------------------------------------
// Hartley's normalisation: shift the points to their centre and scale them to a mean distance of sqrt(2); returns the 3x3 transform
function hartley(P) {
  let mx = 0, my = 0;
  for (const p of P) (mx += p[0] / P.length), (my += p[1] / P.length);
  let d = 0;
  for (const p of P) d += Math.hypot(p[0] - mx, p[1] - my) / P.length;
  const sc = d > 1e-12 ? Math.SQRT2 / d : 1;
  return [sc, 0, -sc * mx, 0, sc, -sc * my, 0, 0, 1];
}
function essentialFrom(x1, x2) {
  const T1 = hartley(x1), T2 = hartley(x2);
  const rows = x1.map((a, i) => {
    const b = x2[i];
    const p = mv(T1, a), q = mv(T2, b);
    return [q[0] * p[0], q[0] * p[1], q[0], q[1] * p[0], q[1] * p[1], q[1], p[0], p[1], 1];
  });
  const f = nullVec(rows, 9);
  const En = mm(mm(mt(T2), f), T1); // back to bearing coordinates
  const { U, S, V } = svd3(En);
  const s = (S[0] + S[1]) / 2;
  return mm(mm(fromRows3(U), [s, 0, 0, 0, s, 0, 0, 0, 0]), mt(fromRows3(V)));
}
function sampson(E, a, b) {
  const Ea = mv(E, a), Eb = mtv(E, b);
  const num = dot(b, Ea);
  return (num * num) / (Ea[0] * Ea[0] + Ea[1] * Ea[1] + Eb[0] * Eb[0] + Eb[1] * Eb[1] + 1e-30);
}
function homographyFrom(x1, x2) {
  const T1 = hartley(x1), T2 = hartley(x2);
  const rows = [];
  x1.forEach((a0, i) => {
    const a = mv(T1, a0), b = mv(T2, x2[i]);
    rows.push([a[0], a[1], 1, 0, 0, 0, -b[0] * a[0], -b[0] * a[1], -b[0]]);
    rows.push([0, 0, 0, a[0], a[1], 1, -b[1] * a[0], -b[1] * a[1], -b[1]]);
  });
  const Hn = nullVec(rows, 9);
  const Ti = inv3(T2);
  return mm(mm(Ti, Hn), T1);
}
function transferErr(H, a, b) {
  const p = mv(H, a);
  if (Math.abs(p[2]) < 1e-12) return Infinity;
  return (p[0] / p[2] - b[0]) ** 2 + (p[1] / p[2] - b[1]) ** 2;
}

// RANSAC with local optimisation: whenever a sample beats the best, refit on all its inliers a few times (a minimal sample is noisy)
function ransac(rand, n, minimal, fit, err, thr, iters) {
  let best = null;
  const count = (model, t) => {
    const inl = [];
    for (let i = 0; i < n; i++) if (err(model, i) <= t) inl.push(i);
    return inl;
  };
  for (let it = 0; it < iters; it++) {
    let model = fit(sample(rand, n, minimal));
    if (!model) continue;
    let inl = count(model, thr);
    // local optimisation: refit on everything fairly close, tightening step by step
    for (const f of [6, 3, 1.8, 1]) {
      const wide = count(model, thr * f);
      if (wide.length < minimal + 2) break;
      const m2 = fit(wide);
      if (!m2) break;
      const i2 = count(m2, thr);
      if (i2.length >= inl.length) (model = m2), (inl = i2);
    }
    if (!best || inl.length > best.inl.length) best = { model, inl };
    if (best.inl.length >= 0.95 * n && it >= 10) break;
  }
  return best;
}

// candidate relative poses {R, t (unit)} from E; or from H (plane): up to four each
function decomposeE(E) {
  const { U, V } = svd3(E);
  const Um = U.map((r) => Float64Array.from(r)), Vm = V.map((r) => Float64Array.from(r));
  if (det3(fromRows3(Um)) < 0) for (const r of Um) r[2] = -r[2];
  if (det3(fromRows3(Vm)) < 0) for (const r of Vm) r[2] = -r[2];
  const Uf = fromRows3(Um), Vt = mt(fromRows3(Vm));
  const W = [0, -1, 0, 1, 0, 0, 0, 0, 1];
  const R1 = mm(mm(Uf, W), Vt), R2 = mm(mm(Uf, mt(W)), Vt), t = colOf(Um, 2);
  return [{ R: R1, t }, { R: R1, t: [-t[0], -t[1], -t[2]] }, { R: R2, t }, { R: R2, t: [-t[0], -t[1], -t[2]] }];
}
function decomposeH(H0) {
  let H = H0.slice();
  const s2 = svd3(H).S[1] || 1;
  H = H.map((x) => x / s2);
  const { S, V } = svd3(mm(mt(H), H));
  const Vc = [colOf(V, 0), colOf(V, 1), colOf(V, 2)];
  const s1 = Math.max(S[0], 1 + 1e-9), s3 = Math.min(S[2], 1 - 1e-9);
  const den = Math.sqrt(s1 - s3);
  const a = Math.sqrt(1 - s3), b = Math.sqrt(s1 - 1);
  const out = [];
  for (const sg of [1, -1]) {
    const u = [0, 1, 2].map((k) => (a * Vc[0][k] + sg * b * Vc[2][k]) / den);
    const v2 = Vc[1];
    const U1 = cols3(v2, u, cross(v2, u));
    const Hv = mv(H, v2), Hu = mv(H, u);
    const W1 = cols3(Hv, Hu, cross(Hv, Hu));
    const R = mm(W1, mt(U1));
    const N = cross(v2, u);
    const HR = H.map((x, i) => x - R[i]);
    const t = mv(HR, N);
    for (const s of [1, -1]) out.push({ R, t: unit([t[0] * s, t[1] * s, t[2] * s]) });
  }
  return out;
}

// score a pose against the matches: how many points triangulate in front of both cameras with some parallax; also their mean error (bearing units)
function scorePose(R, t, x1, x2, idxs) {
  let good = 0, errSum = 0;
  for (const i of idxs) {
    const a = ray(I3(), [0, 0, 0], x1.get(i)), b = ray(R, t, x2.get(i));
    const tr = triangulate([a, b]);
    if (!tr.ok || tr.maxAngle < 0.5) continue;
    const pr = project(R, t, 1, tr.X);
    if (!pr) continue;
    good++;
    errSum += Math.hypot(pr[0] - x2.get(i)[0], pr[1] - x2.get(i)[1]);
  }
  return { good, err: good ? errSum / good : Infinity };
}

// initial candidate poses for views a, b: [{R, t, planar}] best first
function initialPoses(va, vb, shared, F, rand, tolPx) {
  const x1 = new Map(), x2 = new Map();
  for (const i of shared) {
    x1.set(i, bearing(va.pts.get(i), F));
    x2.set(i, bearing(vb.pts.get(i), F));
  }
  const idxs = shared, N = idxs.length;
  const A = idxs.map((i) => x1.get(i)), B = idxs.map((i) => x2.get(i));
  const thr = (tolPx / (Math.max(va.m, vb.m) * F)) ** 2;
  const out = [];
  // essential matrix
  const eBest = N >= 8 ? ransac(rand, N, 8, (s) => essentialFrom(s.map((k) => A[k]), s.map((k) => B[k])), (E, k) => sampson(E, A[k], B[k]), thr, 400) : null;
  const hBest = N >= 4 ? ransac(rand, N, 4, (s) => homographyFrom(s.map((k) => A[k]), s.map((k) => B[k])), (H, k) => transferErr(H, A[k], B[k]), thr * 1.5, 300) : null;
  const planar = hBest && hBest.inl.length >= 0.9 * N;
  if (eBest && eBest.inl.length >= 8 && !planar) {
    const E = essentialFrom(eBest.inl.map((k) => A[k]), eBest.inl.map((k) => B[k]));
    const cands = decomposeE(E).map((c) => ({ ...c, ...scorePose(c.R, c.t, x1, x2, eBest.inl.map((k) => idxs[k])), planar: false }));
    cands.sort((p, q) => q.good - p.good || p.err - q.err);
    if (cands[0].good >= 6) out.push(cands[0]);
  }
  if (hBest && hBest.inl.length >= 4 && (planar || !out.length)) {
    const H = homographyFrom(hBest.inl.map((k) => A[k]), hBest.inl.map((k) => B[k]));
    let sign = 0;
    for (const k of hBest.inl) sign += dot(B[k], mv(H, A[k]));
    const Hs = sign < 0 ? H.map((x) => -x) : H;
    const inl = hBest.inl.map((k) => idxs[k]);
    const cands = decomposeH(Hs).map((c) => ({ ...c, ...scorePose(c.R, c.t, x1, x2, inl), planar: true }));
    cands.sort((p, q) => q.good - p.good || p.err - q.err);
    const top = cands[0] ? cands[0].good : 0;
    for (const c of cands) if (c.good >= Math.max(4, 0.8 * top) && !out.some((o) => angleBetween(o.R, c.R) < 1 && norm(sub(o.t, c.t)) < 0.05)) out.push(c);
  }
  return out.slice(0, 3);
}

// the focal length is only a guess: try a few around it and keep the one that explains the most matches
function initialPosesF(va, vb, shared, F0, rand, tolPx) {
  let best = null;
  const tryF = (F) => {
    const c = initialPoses(va, vb, shared, F, rand, tolPx);
    if (!c.length) return;
    c.forEach((x) => (x.F = F));
    if (!best || c[0].good > best.score) best = { score: c[0].good, c, F };
  };
  tryF(F0);
  if (best && best.score >= 0.85 * shared.length) return best.c;
  for (const k of [0.92, 1.08, 0.84, 1.16, 0.74, 1.3]) tryF(F0 * k);
  if (!best) return [];
  for (const k of [0.97, 1.03, 0.985, 1.015]) tryF(best.F * k); // then narrow in on it
  return best.c;
}

// ---- adding a view -------------------------------------------------------------------------------------------------------------
// pose from known 3D points: general (DLT) or flat scenes (plane homography). pts: [{X, b (bearing)}]. Returns {R, t, inl} or null.
function poseDLT(P, minimal = 6) {
  const mu = [0, 0, 0];
  for (const p of P) for (let k = 0; k < 3; k++) mu[k] += p.X[k] / P.length;
  let sc = 0;
  for (const p of P) sc += norm(sub(p.X, mu)) / P.length;
  sc = sc || 1;
  const rows = [];
  for (const p of P) {
    const X = [(p.X[0] - mu[0]) / sc, (p.X[1] - mu[1]) / sc, (p.X[2] - mu[2]) / sc, 1], x = p.b[0], y = p.b[1];
    rows.push([...X, 0, 0, 0, 0, ...X.map((q) => -x * q)]);
    rows.push([0, 0, 0, 0, ...X, ...X.map((q) => -y * q)]);
  }
  if (rows.length < minimal * 2) return null;
  let M = nullVec(rows, 12);
  let A3 = [M[0], M[1], M[2], M[4], M[5], M[6], M[8], M[9], M[10]];
  if (det3(A3) < 0) {
    M = M.map((q) => -q);
    A3 = A3.map((q) => -q);
  }
  const s = svd3(A3).S;
  const k = (s[0] + s[1] + s[2]) / 3;
  if (k < 1e-12 || s[2] / s[0] < 0.05) return null;
  const R = orthogonalise(A3);
  const m4 = [M[3], M[7], M[11]];
  const tt = m4.map((q) => (sc * q) / k);
  return { R, t: sub(tt, mv(R, mu)) };
}
function posePlane(P) {
  const mu = [0, 0, 0];
  for (const p of P) for (let k = 0; k < 3; k++) mu[k] += p.X[k] / P.length;
  const C = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (const p of P) {
    const d = sub(p.X, mu);
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) C[i][j] += d[i] * d[j];
  }
  const { V } = svd(C);
  const e1 = colOf(V, 0), e2 = colOf(V, 1), e3 = cross(e1, e2);
  const Pm = cols3(e1, e2, e3); // plane coordinates p = Pm^T (X - mu)
  const x1 = P.map((q) => {
    const d = mtv(Pm, sub(q.X, mu));
    return [d[0], d[1], 1];
  });
  const H = homographyFrom(x1, P.map((q) => q.b));
  let h1 = [H[0], H[3], H[6]], h2 = [H[1], H[4], H[7]], h3 = [H[2], H[5], H[8]];
  const lam = 1 / ((norm(h1) + norm(h2)) / 2 || 1);
  if (h3[2] * lam < 0) {
    h1 = h1.map((q) => -q);
    h2 = h2.map((q) => -q);
    h3 = h3.map((q) => -q);
  }
  h1 = h1.map((q) => q * lam);
  h2 = h2.map((q) => q * lam);
  h3 = h3.map((q) => q * lam);
  const Rp = orthogonalise(cols3(h1, h2, cross(h1, h2)));
  const R = mm(Rp, mt(Pm));
  return { R, t: sub(h3, mv(R, mu)) };
}
function planarity(P) {
  const mu = [0, 0, 0];
  for (const p of P) for (let k = 0; k < 3; k++) mu[k] += p.X[k] / P.length;
  const C = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (const p of P) {
    const d = sub(p.X, mu);
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) C[i][j] += d[i] * d[j];
  }
  const s = svd(C).S;
  return Math.sqrt(s[2] / (s[0] || 1));
}
function registerPnP(P, F, rand, tol, prior) {
  const N = P.length;
  if (N < 6) return null;
  const err = (pose, k) => {
    const pr = project(pose.R, pose.t, 1, P[k].X);
    return pr ? (pr[0] - P[k].b[0]) ** 2 + (pr[1] - P[k].b[1]) ** 2 : Infinity;
  };
  const flat = planarity(P) < 0.05;
  const tries = flat ? [[4, (s) => posePlane(s.map((k) => P[k]))], [6, (s) => poseDLT(s.map((k) => P[k]))]] : [[6, (s) => poseDLT(s.map((k) => P[k]))], [4, (s) => posePlane(s.map((k) => P[k]))]];
  let best = null;
  for (const [minimal, fit] of tries) {
    const r = ransac(rand, N, minimal, (s) => {
      try {
        return fit(s);
      } catch (e) {
        return null;
      }
    }, err, tol, 250);
    if (r && (!best || r.inl.length > best.inl.length * 1.05)) best = r;
    if (best && best.inl.length >= 0.8 * N) break;
  }
  if (!best || best.inl.length < 6) return null;
  // refit on the inliers
  const sub2 = best.inl.map((k) => P[k]);
  let pose = flat ? posePlane(sub2) : poseDLT(sub2);
  if (!pose || best.inl.filter((k) => err(pose, k) <= tol).length < best.inl.length * 0.9) pose = best.model;
  void prior;
  return { R: pose.R, t: pose.t, inl: best.inl.length };
}

// ---- bundle adjustment ------------------------------------------------------------------------------------------------------------
// st: {F, R[v], t[v], reg:Set, ref, X (Float64Array 3n), has (Uint8Array n), bad:Set(v*n+i)}; views[v].pts normalised obs.
function bundleAdjust(st, views, n, o = {}) {
  const freeViews = [...st.reg].filter((v) => v !== st.ref);
  const off = new Map(freeViews.map((v, k) => [v, 6 * k]));
  const freeF = o.freeF !== false && st.reg.size >= 3; // two views cannot tell the focal length from the scene depth
  const wF = (0.0005 / (0.3 * st.F0)) ** 2; // a weak pull towards the guess (30% sigma) so it can't run away
  const P = 6 * freeViews.length + (freeF ? 1 : 0);
  const fcol = freeF ? P - 1 : -1;
  const obs = [];
  const count = new Uint8Array(n);
  for (const v of st.reg) for (const [i, p] of views[v].pts) if (st.has[i] && !st.bad.has(v * n + i)) (obs.push({ v, i, u: p[0], w: p[1] }), count[i]++);
  const live = obs.filter((q) => count[q.i] >= 2);
  const slot = new Map();
  for (const q of live) if (!slot.has(q.i)) slot.set(q.i, slot.size);
  const NP = slot.size;
  if (!live.length) return { rms: 0, n: 0 };
  const delta = o.huber ?? 0.0015; // normalised units (about 3 px on a 1920 picture)
  const eps = 1e-6;
  const dR = [0, 1, 2].map((j) => rodrigues([j === 0 ? eps : 0, j === 1 ? eps : 0, j === 2 ? eps : 0]));
  const cost = (R, t, F, X) => {
    let c = freeF ? 0.5 * wF * (F - st.F0) ** 2 : 0;
    for (const q of live) {
      const pr = project(R[q.v], t[q.v], F, [X[3 * q.i], X[3 * q.i + 1], X[3 * q.i + 2]]);
      if (!pr) return Infinity;
      const r = Math.hypot(q.u - pr[0], q.w - pr[1]);
      c += r <= delta ? 0.5 * r * r : delta * (r - 0.5 * delta);
    }
    return c;
  };
  let lambda = o.lambda ?? 1e-3;
  let cur = cost(st.R, st.t, st.F, st.X);
  for (let it = 0; it < (o.iters ?? 15); it++) {
    const U = Array.from({ length: P }, () => new Float64Array(P)), eC = new Float64Array(P);
    const Vp = new Float64Array(9 * NP), eP = new Float64Array(3 * NP), Wp = new Float64Array(P * 3 * NP);
    for (const q of live) {
      const R = st.R[q.v], t = st.t[q.v], X = [st.X[3 * q.i], st.X[3 * q.i + 1], st.X[3 * q.i + 2]];
      const base = project(R, t, st.F, X);
      const e0 = q.u - base[0], e1 = q.w - base[1], r = Math.hypot(e0, e1);
      const sw = Math.sqrt(r <= delta ? 1 : delta / r);
      const o6 = off.get(q.v);
      const cc = [];
      const Jc = [[], []];
      if (o6 !== undefined) {
        for (let j = 0; j < 3; j++) {
          const p = project(mm(dR[j], R), t, st.F, X);
          cc.push(o6 + j);
          Jc[0].push(((p[0] - base[0]) / eps) * sw);
          Jc[1].push(((p[1] - base[1]) / eps) * sw);
        }
        for (let j = 0; j < 3; j++) {
          const tp = t.slice();
          tp[j] += eps;
          const p = project(R, tp, st.F, X);
          cc.push(o6 + 3 + j);
          Jc[0].push(((p[0] - base[0]) / eps) * sw);
          Jc[1].push(((p[1] - base[1]) / eps) * sw);
        }
      }
      if (freeF) {
        const p = project(R, t, st.F + eps, X);
        cc.push(fcol);
        Jc[0].push(((p[0] - base[0]) / eps) * sw);
        Jc[1].push(((p[1] - base[1]) / eps) * sw);
      }
      const Jp = [[0, 0, 0], [0, 0, 0]];
      for (let j = 0; j < 3; j++) {
        const Xp = X.slice();
        Xp[j] += eps;
        const p = project(R, t, st.F, Xp);
        Jp[0][j] = ((p[0] - base[0]) / eps) * sw;
        Jp[1][j] = ((p[1] - base[1]) / eps) * sw;
      }
      const e = [e0 * sw, e1 * sw], s = slot.get(q.i);
      for (let row = 0; row < 2; row++) {
        for (let a = 0; a < cc.length; a++) {
          const ja = Jc[row][a];
          eC[cc[a]] += ja * e[row];
          for (let b = 0; b < cc.length; b++) U[cc[a]][cc[b]] += ja * Jc[row][b];
          for (let c = 0; c < 3; c++) Wp[(s * P + cc[a]) * 3 + c] += ja * Jp[row][c];
        }
        for (let a = 0; a < 3; a++) {
          eP[3 * s + a] += Jp[row][a] * e[row];
          for (let b = 0; b < 3; b++) Vp[9 * s + 3 * a + b] += Jp[row][a] * Jp[row][b];
        }
      }
    }
    if (freeF) {
      U[fcol][fcol] += wF;
      eC[fcol] += wF * (st.F0 - st.F);
    }
    let improved = false;
    for (let attempt = 0; attempt < 8 && !improved; attempt++) {
      const S = U.map((row, i) => {
        const r = Float64Array.from(row);
        r[i] += lambda * Math.max(row[i], 1e-9);
        return r;
      });
      const rhs = Float64Array.from(eC);
      const Vinv = new Array(NP);
      for (let s = 0; s < NP; s++) {
        const V = [Vp[9 * s], Vp[9 * s + 1], Vp[9 * s + 2], Vp[9 * s + 3], Vp[9 * s + 4], Vp[9 * s + 5], Vp[9 * s + 6], Vp[9 * s + 7], Vp[9 * s + 8]];
        for (let a = 0; a < 3; a++) V[a * 4] += lambda * Math.max(V[a * 4], 1e-9);
        const Vi = inv3(V);
        Vinv[s] = Vi;
        if (!Vi) continue;
        // Y = W Vi (P x 3); S -= Y W^T; rhs -= Y eP
        for (let a = 0; a < P; a++) {
          const w0 = Wp[(s * P + a) * 3], w1 = Wp[(s * P + a) * 3 + 1], w2 = Wp[(s * P + a) * 3 + 2];
          if (w0 === 0 && w1 === 0 && w2 === 0) continue;
          const y0 = w0 * Vi[0] + w1 * Vi[3] + w2 * Vi[6], y1 = w0 * Vi[1] + w1 * Vi[4] + w2 * Vi[7], y2 = w0 * Vi[2] + w1 * Vi[5] + w2 * Vi[8];
          rhs[a] -= y0 * eP[3 * s] + y1 * eP[3 * s + 1] + y2 * eP[3 * s + 2];
          for (let b = 0; b < P; b++) S[a][b] -= y0 * Wp[(s * P + b) * 3] + y1 * Wp[(s * P + b) * 3 + 1] + y2 * Wp[(s * P + b) * 3 + 2];
        }
      }
      const dc = P ? solveLin(S, rhs) : new Float64Array(0);
      if (!dc) {
        lambda *= 10;
        continue;
      }
      // points
      const nR = st.R.slice(), nt = st.t.slice(), nX = Float64Array.from(st.X);
      let nF = st.F;
      for (const [v, o6] of off) {
        nR[v] = mm(rodrigues([dc[o6], dc[o6 + 1], dc[o6 + 2]]), st.R[v]);
        nt[v] = [st.t[v][0] + dc[o6 + 3], st.t[v][1] + dc[o6 + 4], st.t[v][2] + dc[o6 + 5]];
      }
      if (freeF) nF = Math.max(0.45, Math.min(1.6, st.F + dc[fcol]));
      for (const [i, s] of slot) {
        const Vi = Vinv[s];
        if (!Vi) continue;
        const rr = [eP[3 * s], eP[3 * s + 1], eP[3 * s + 2]];
        for (let a = 0; a < P; a++) for (let c = 0; c < 3; c++) rr[c] -= Wp[(s * P + a) * 3 + c] * dc[a];
        const d = mv(Vi, rr);
        nX[3 * i] += d[0];
        nX[3 * i + 1] += d[1];
        nX[3 * i + 2] += d[2];
      }
      const c2 = cost(nR, nt, nF, nX);
      if (c2 < cur) {
        const rel = (cur - c2) / (cur + 1e-30);
        st.R = nR;
        st.t = nt;
        st.X = nX;
        st.F = nF;
        cur = c2;
        lambda = Math.max(lambda / 4, 1e-9);
        improved = true;
        if (rel < 1e-7) it = 1e9;
      } else lambda *= 4;
    }
    if (!improved) break;
  }
  return reprojection(st, views, n);
}

// per-observation errors in pixels, rms overall and per view
function reprojection(st, views, n) {
  const per = new Map(), sums = new Map();
  let s = 0, c = 0;
  for (const v of st.reg) {
    let sv = 0, cv = 0;
    for (const [i, p] of views[v].pts) {
      if (!st.has[i] || st.bad.has(v * n + i)) continue;
      const pr = project(st.R[v], st.t[v], st.F, [st.X[3 * i], st.X[3 * i + 1], st.X[3 * i + 2]]);
      const e = pr ? Math.hypot(p[0] - pr[0], p[1] - pr[1]) * views[v].m : 1e9;
      per.set(v * n + i, e);
      sv += e * e;
      cv++;
    }
    sums.set(v, { sq: sv, n: cv });
    s += sv;
    c += cv;
  }
  return { rms: c ? Math.sqrt(s / c) : 0, n: c, per, perView: sums };
}

// ---- incremental reconstruction -----------------------------------------------------------------------------------------------------
function triangulateNew(st, views, n, tolPx, minAngle = 1.0) {
  let added = 0;
  for (let i = 0; i < n; i++) {
    if (st.has[i]) continue;
    const list = [];
    for (const v of st.reg) if (views[v].pts.has(i) && !st.bad.has(v * n + i)) list.push(v);
    if (list.length < 2) continue;
    for (let round = 0; round < 3 && list.length >= 2; round++) {
      const rays = list.map((v) => ray(st.R[v], st.t[v], bearing(views[v].pts.get(i), st.F)));
      const tr = triangulate(rays);
      if (!tr.X || !tr.ok || tr.maxAngle < minAngle) break;
      let worst = -1, we = 0;
      list.forEach((v, k) => {
        const pr = project(st.R[v], st.t[v], st.F, tr.X);
        const e = pr ? Math.hypot(views[v].pts.get(i)[0] - pr[0], views[v].pts.get(i)[1] - pr[1]) * views[v].m : 1e9;
        if (e > we) (we = e), (worst = k);
      });
      if (we <= tolPx) {
        st.X[3 * i] = tr.X[0];
        st.X[3 * i + 1] = tr.X[1];
        st.X[3 * i + 2] = tr.X[2];
        st.has[i] = 1;
        added++;
        break;
      }
      list.splice(worst, 1);
    }
  }
  return added;
}

function dropOutliers(st, views, n, rep, tolPx) {
  const errs = [...rep.per.values()];
  const med = median(errs);
  const cut = Math.max(tolPx, 4 * med);
  let dropped = 0;
  for (const [key, e] of rep.per) {
    if (e > cut) {
      st.bad.add(key);
      dropped++;
    }
  }
  // a point needs two good views
  for (let i = 0; i < n; i++) {
    if (!st.has[i]) continue;
    let c = 0;
    for (const v of st.reg) if (views[v].pts.has(i) && !st.bad.has(v * n + i)) c++;
    if (c < 2) st.has[i] = 0;
  }
  return dropped;
}

function reconstruct(views, n, a, b, pose, F0, rand, o) {
  const V = views.length;
  const st = { F: F0, F0, R: new Array(V).fill(null), t: new Array(V).fill(null), reg: new Set([a, b]), ref: a, X: new Float64Array(3 * n), has: new Uint8Array(n), bad: new Set() };
  st.R[a] = I3();
  st.t[a] = [0, 0, 0];
  st.R[b] = pose.R;
  st.t[b] = pose.t;
  const tol = o.tolPx;
  // the focal length is only a guess at first, so be generous; bundle adjustment then fixes it and the tight check follows
  triangulateNew(st, views, n, tol * 8, 0.5);
  if (st.has.reduce((x, y) => x + y, 0) < 6) return null;
  let rep = bundleAdjust(st, views, n, { iters: 15, huber: 0.004 });
  rep = bundleAdjust(st, views, n, { iters: 10 });
  dropOutliers(st, views, n, rep, tol * 2);
  triangulateNew(st, views, n, tol * 2, 0.5);
  rep = bundleAdjust(st, views, n, { iters: 10 });
  dropOutliers(st, views, n, rep, tol);
  rep = bundleAdjust(st, views, n, { iters: 10 });
  const failed = new Set();
  for (;;) {
    // the unregistered view that sees the most triangulated points
    let bestV = -1, bestN = 0;
    for (let v = 0; v < V; v++) {
      if (st.reg.has(v) || failed.has(v)) continue;
      let c = 0;
      for (const i of views[v].pts.keys()) if (st.has[i]) c++;
      if (c > bestN) (bestN = c), (bestV = v);
    }
    if (bestV < 0 || bestN < 6) break;
    const P = [];
    for (const [i, p] of views[bestV].pts) if (st.has[i]) P.push({ X: [st.X[3 * i], st.X[3 * i + 1], st.X[3 * i + 2]], b: bearing(p, st.F) });
    const thr = (tol * 3 / (views[bestV].m * st.F)) ** 2;
    const reg = registerPnP(P, st.F, rand, thr * 1.0, null);
    if (!reg || reg.inl < Math.max(6, 0.3 * P.length)) {
      failed.add(bestV); // cannot place this one: leave it out and try the others
      continue;
    }
    st.R[bestV] = reg.R;
    st.t[bestV] = reg.t;
    st.reg.add(bestV);
    triangulateNew(st, views, n, tol * 2, 1.0);
    rep = bundleAdjust(st, views, n, { iters: 10 });
    dropOutliers(st, views, n, rep, tol);
    rep = bundleAdjust(st, views, n, { iters: 8 });
  }
  triangulateNew(st, views, n, tol * 2, 1.0);
  rep = bundleAdjust(st, views, n, { iters: 20 });
  dropOutliers(st, views, n, rep, tol);
  triangulateNew(st, views, n, tol, 1.0);
  rep = bundleAdjust(st, views, n, { iters: 25, lambda: 1e-4 });
  const dropped = dropOutliers(st, views, n, rep, tol);
  if (dropped) rep = bundleAdjust(st, views, n, { iters: 10, lambda: 1e-4 });
  return { st, rep };
}

// ---- output ---------------------------------------------------------------------------------------------------------------------------------
// rotation that carries unit vector a onto b
function rotateOnto(a, b) {
  const c = cross(a, b), s = norm(c), d = dot(a, b);
  if (s < 1e-9) return d > 0 ? I3() : rodrigues([Math.PI, 0, 0]);
  return rodrigues(c.map((q) => (q / s) * Math.atan2(s, d)));
}

// Solve. views as in the header; opts {n (LED count), fovDeg, tolPx (default 3), seed}.
// Returns {ok, error?, pos (Float32Array 3n, 0-1 per axis, y up, z towards the first camera), est (Uint8Array n: 1 = placed by string order or
// a single view), count (Uint8Array n: views that saw each LED), used (Uint8Array n: views that went into the 3D point), views: [{ok, rms, count, center,
// forward, R}], rms, fovDeg, found (lights triangulated), pruned, n, leveled}
export function solveMultiView(viewsIn, opts = {}) {
  let n = opts.n ?? 0;
  if (!n) for (const v of viewsIn) for (const k of v.points instanceof Map ? v.points.keys() : Object.keys(v.points || {})) n = Math.max(n, Number(k) + 1);
  const fail = (error) => ({ ok: false, error, n, views: viewsIn.map(() => ({ ok: false })), pos: new Float32Array(3 * n), est: new Uint8Array(n).fill(1), count: coverage(viewsIn, n).seen, found: 0 });
  if (viewsIn.length < 2) return fail("Need at least two views.");
  const views = viewsIn.map((v) => prepView(v, n));
  const V = views.length;
  const fov = opts.fovDeg ?? views.find((v) => v.fovDeg)?.fovDeg ?? 65;
  const F0 = fovToF(fov);
  const tolPx = opts.tolPx ?? 3;
  const rand = rng(opts.seed ?? 12345);
  // pairs by shared points
  const pairs = [];
  for (let a = 0; a < V; a++) {
    for (let b = a + 1; b < V; b++) {
      const shared = [];
      for (const i of views[a].pts.keys()) if (views[b].pts.has(i)) shared.push(i);
      if (shared.length >= 8) pairs.push({ a, b, shared });
    }
  }
  if (!pairs.length) return fail("The views share too few lights (need at least 8 seen in two views).");
  pairs.sort((p, q) => q.shared.length - p.shared.length);
  let best = null;
  const better = (r, x) => !x || r.st.reg.size > x.st.reg.size || (r.st.reg.size === x.st.reg.size && (r.rep.rms < x.rep.rms - 1e-9 || false));
  for (const pr of pairs.slice(0, 3)) {
    const cands = initialPosesF(views[pr.a], views[pr.b], pr.shared, F0, rand, tolPx * 1.5);
    for (const c of cands) {
      const r = reconstruct(views, n, pr.a, pr.b, c, c.F ?? F0, rand, { tolPx });
      if (!r) continue;
      // flat scenes have two look-alike answers; the one agreeing with the phone's orientation wins a tie
      r.prior = 0;
      if (views[pr.a].Rcw && views[pr.b].Rcw) {
        const Rrel = mm(mt(views[pr.b].Rcw), views[pr.a].Rcw);
        r.prior = angleBetween(Rrel, r.st.R[pr.b]);
      }
      if (better(r, best) || (best && r.st.reg.size === best.st.reg.size && Math.abs(r.rep.rms - best.rep.rms) < 0.15 * Math.max(r.rep.rms, 0.1) && r.prior < best.prior - 10)) best = r;
    }
    if (best && best.st.reg.size === V && best.rep.rms < tolPx) break;
  }
  if (!best) return fail("Couldn't work out where the camera moved. Scan from angles that see many of the same lights, and keep them well apart.");
  const { st, rep } = best;
  // express everything in the frame of the first view (the first one placed, if view 1 could not be)
  const ref = [...st.reg].sort((p, q) => p - q)[0];
  if (ref !== st.ref) {
    const R0 = st.R[ref], t0 = st.t[ref];
    for (const v of st.reg) {
      st.R[v] = mm(st.R[v], mt(R0));
      st.t[v] = sub(st.t[v], mv(st.R[v], t0));
    }
    for (let i = 0; i < n; i++) {
      if (!st.has[i]) continue;
      const q = mv(R0, [st.X[3 * i], st.X[3 * i + 1], st.X[3 * i + 2]]);
      st.X[3 * i] = q[0] + t0[0];
      st.X[3 * i + 1] = q[1] + t0[1];
      st.X[3 * i + 2] = q[2] + t0[2];
    }
    st.ref = ref;
  }
  const found0 = st.has.reduce((x, y) => x + y, 0);
  if (found0 < 8) return fail("Too few lights were seen from two angles.");

  // level the scene with gravity when the phones reported it: up in the reference camera frame (y down there)
  let leveled = false, Rl = I3();
  const sumUp = [0, 0, 0];
  let nUp = 0;
  for (const v of st.reg) {
    if (!views[v].Rcw) continue;
    const u = mtv(st.R[v], mtv(views[v].Rcw, [0, 0, 1])); // world up in this camera's axes, carried back to the reference camera
    for (let k = 0; k < 3; k++) sumUp[k] += u[k];
    nUp++;
  }
  if (nUp >= 2 && norm(sumUp) > 0.5 * nUp) {
    Rl = rotateOnto(unit(sumUp), [0, -1, 0]);
    leveled = true;
  }
  // reference frame (x right, y down, z forward) levelled -> output frame (x right, y up, z towards the camera)
  const FLIP = [1, 0, 0, 0, -1, 0, 0, 0, -1];
  const T = mm(FLIP, Rl); // reference-frame vector -> output frame
  const pts = new Float64Array(3 * n);
  for (let i = 0; i < n; i++) {
    if (!st.has[i]) continue;
    const q = mv(T, [st.X[3 * i], st.X[3 * i + 1], st.X[3 * i + 2]]);
    pts[3 * i] = q[0];
    pts[3 * i + 1] = q[1];
    pts[3 * i + 2] = q[2];
  }
  // A light seen from only two views can be a wrong match that happens to sit on its epipolar line. Drop solid points that are far from
  // everything else, or far off the line their string neighbours make (the string is smooth).
  let pruned = 0;
  {
    const idx = [];
    for (let i = 0; i < n; i++) if (st.has[i]) idx.push(i);
    if (idx.length >= 12) {
      const cen = [0, 1, 2].map((c) => median(idx.map((i) => pts[3 * i + c])));
      const dists = idx.map((i) => Math.hypot(pts[3 * i] - cen[0], pts[3 * i + 1] - cen[1], pts[3 * i + 2] - cen[2]));
      const far = 3 * [...dists].sort((p, q) => p - q)[Math.floor(dists.length * 0.9)];
      idx.forEach((i, k) => {
        if (dists[k] > far) (st.has[i] = 0), pruned++;
      });
      const live = idx.filter((i) => st.has[i]);
      const steps = [];
      for (let k = 0; k + 1 < live.length; k++) if (live[k + 1] - live[k] === 1) steps.push(Math.hypot(pts[3 * live[k + 1]] - pts[3 * live[k]], pts[3 * live[k + 1] + 1] - pts[3 * live[k] + 1], pts[3 * live[k + 1] + 2] - pts[3 * live[k] + 2]));
      const step = median(steps);
      if (step > 0) {
        for (let k = 1; k + 1 < live.length; k++) {
          const p = live[k - 1], q = live[k + 1], i = live[k];
          if (q - p > 6) continue;
          const f = (i - p) / (q - p);
          let dev = 0;
          for (let c = 0; c < 3; c++) dev += (pts[3 * i + c] - (pts[3 * p + c] + (pts[3 * q + c] - pts[3 * p + c]) * f)) ** 2;
          if (Math.sqrt(dev) > 6 * step * (q - p)) (st.has[i] = 0), pruned++;
        }
      }
    }
  }
  // lights seen once, or never, or pruned: a light seen in some view keeps its place in the picture and takes its depth from its string
  // neighbours (or the view's middle depth); one seen nowhere is put on the line between its string neighbours
  const est = new Uint8Array(n);
  const solid = [];
  for (let i = 0; i < n; i++) if (st.has[i]) solid.push(i);
  const camZ = (v, i) => {
    const p = mv(st.R[v], [st.X[3 * i], st.X[3 * i + 1], st.X[3 * i + 2]]);
    return p[2] + st.t[v][2];
  };
  const midDepth = new Map();
  const depthIn = (v) => {
    if (!midDepth.has(v)) midDepth.set(v, median(solid.filter((i) => views[v].pts.has(i)).map((i) => camZ(v, i))) || 1);
    return midDepth.get(v);
  };
  let k = 0;
  for (let i = 0; i < n; i++) {
    if (st.has[i]) continue;
    est[i] = 1;
    while (k + 1 < solid.length && solid[k + 1] < i) k++;
    const lo = solid.length && solid[k] < i ? solid[k] : -1;
    let hi = -1;
    for (let q = Math.max(k, 0); q < solid.length; q++) if (solid[q] > i) (hi = solid[q]), (q = solid.length);
    const seenIn = [...st.reg].filter((v) => views[v].pts.has(i));
    if (seenIn.length) {
      const v = seenIn[0];
      const dl = lo >= 0 && views[v].pts.has(lo) ? camZ(v, lo) : null, dh = hi >= 0 && views[v].pts.has(hi) ? camZ(v, hi) : null;
      let d;
      if (dl !== null && dh !== null && hi - lo <= 200) d = dl + (dh - dl) * ((i - lo) / (hi - lo));
      else d = dl ?? dh ?? depthIn(v);
      const b = bearing(views[v].pts.get(i), st.F);
      const q = mv(T, mtv(st.R[v], sub([b[0] * d, b[1] * d, d], st.t[v])));
      pts[3 * i] = q[0];
      pts[3 * i + 1] = q[1];
      pts[3 * i + 2] = q[2];
    } else if (lo >= 0 && hi >= 0 && hi - lo <= (opts.maxBridge ?? 60)) {
      const f = (i - lo) / (hi - lo);
      for (let c = 0; c < 3; c++) pts[3 * i + c] = pts[3 * lo + c] + (pts[3 * hi + c] - pts[3 * lo + c]) * f;
    } else {
      const near = lo >= 0 && (hi < 0 || i - lo <= hi - i) ? lo : hi; // beyond the ends: the nearest solid light's place
      for (let c = 0; c < 3; c++) pts[3 * i + c] = near >= 0 ? pts[3 * near + c] : 0;
    }
  }
  // fit into the unit box with the biggest extent spanning 90% (solid points decide the extents)
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const i of solid) for (let c = 0; c < 3; c++) (lo[c] = Math.min(lo[c], pts[3 * i + c])), (hi[c] = Math.max(hi[c], pts[3 * i + c]));
  const ext = Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2], 1e-9), kk = 0.9 / ext, ctr = [0, 1, 2].map((c) => (lo[c] + hi[c]) / 2);
  const pos = new Float32Array(3 * n);
  for (let i = 0; i < n; i++) for (let c = 0; c < 3; c++) pos[3 * i + c] = 0.5 + (pts[3 * i + c] - ctr[c]) * kk;
  const used = new Uint8Array(n);
  for (const v of st.reg) for (const i of views[v].pts.keys()) if (st.has[i] && !st.bad.has(v * n + i)) used[i]++;
  const outViews = views.map((vw, v) => {
    if (!st.reg.has(v)) return { ok: false, count: vw.pts.size };
    const Rout = mm(st.R[v], mt(T)); // output frame -> camera
    const c0 = mtv(st.R[v], [-st.t[v][0], -st.t[v][1], -st.t[v][2]]);
    const cOut = mv(T, c0);
    const fwd = mv(T, mtv(st.R[v], [0, 0, 1]));
    const pv = rep.perView.get(v);
    return {
      ok: true,
      rms: pv && pv.n ? Math.sqrt(pv.sq / pv.n) : 0,
      count: vw.pts.size,
      used: pv ? pv.n : 0,
      center: [0.5 + (cOut[0] - ctr[0]) * kk, 0.5 + (cOut[1] - ctr[1]) * kk, 0.5 + (cOut[2] - ctr[2]) * kk],
      forward: fwd,
      R: Rout,
      priorDeg: views[v].Rcw && views[best.st.ref].Rcw ? angleBetween(mm(mt(views[v].Rcw), views[best.st.ref].Rcw), st.R[v]) : null,
    };
  });
  return { ok: true, n, pos, est, count: coverage(viewsIn, n).seen, used, views: outViews, rms: rep.rms, fovDeg: fToFov(st.F), found: solid.length, pruned, leveled, ref: st.ref };
}

// ---- similarity alignment (tests, and for comparing two results) -----------------------------------------------------------------------------
// Best scale s, rotation R (det +1) and shift t with dst ~ s R src + t (Umeyama). src, dst: arrays of [x, y, z].
export function alignSimilarity(src, dst) {
  const n = src.length;
  const ms = [0, 0, 0], md = [0, 0, 0];
  for (let i = 0; i < n; i++) for (let c = 0; c < 3; c++) (ms[c] += src[i][c] / n), (md[c] += dst[i][c] / n);
  const C = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  let vs = 0;
  for (let i = 0; i < n; i++) {
    const a = sub(src[i], ms), b = sub(dst[i], md);
    vs += dot(a, a) / n;
    for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) C[r][c] += (b[r] * a[c]) / n;
  }
  const { U, S, V } = svd(C);
  const Uf = fromRows3(U), Vf = fromRows3(V);
  const d = det3(Uf) * det3(Vf) < 0 ? -1 : 1;
  const D = [1, 0, 0, 0, 1, 0, 0, 0, d];
  const R = mm(mm(Uf, D), mt(Vf));
  const s = (S[0] + S[1] + d * S[2]) / (vs || 1);
  const t = sub(md, mv(R, ms).map((q) => q * s));
  return { s, R, t, apply: (p) => mv(R, p).map((q, c) => q * s + t[c]) };
}
