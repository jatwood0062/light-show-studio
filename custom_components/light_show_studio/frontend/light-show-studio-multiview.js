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

// 2D points not (nearly) on one line: ratio of the spread across the best line to the spread along it
function spreadOK(P, min = 0.04) {
  let mx = 0, my = 0;
  for (const p of P) (mx += p[0]), (my += p[1]);
  mx /= P.length;
  my /= P.length;
  let a = 0, b = 0, c = 0;
  for (const p of P) {
    const x = p[0] - mx, y = p[1] - my;
    a += x * x;
    b += x * y;
    c += y * y;
  }
  const tr = a + c, dt = Math.sqrt(Math.max(0, (a - c) * (a - c) / 4 + b * b));
  const l1 = tr / 2 + dt, l2 = tr / 2 - dt;
  return l1 > 1e-18 && Math.sqrt(Math.max(l2, 0) / l1) > min;
}
// 3D points not (nearly) on one line
function lineLike3(P, min = 0.03) {
  const mu = [0, 0, 0];
  for (const p of P) for (let k = 0; k < 3; k++) mu[k] += p[k] / P.length;
  const C = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (const p of P) {
    const d = sub(p, mu);
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) C[i][j] += d[i] * d[j];
  }
  const sv = svd(C).S;
  return Math.sqrt(sv[1] / (sv[0] || 1)) < min;
}
// index -> run number (0 for everything when there are no runs); runs = [[start, count], ...]
function runIndex(n, runs) {
  const r = new Int32Array(n).fill(-1);
  if (!runs || !runs.length) return r.fill(0);
  runs.forEach((q, k) => {
    for (let i = Math.max(0, q[0]); i < Math.min(n, q[0] + q[1]); i++) r[i] = k;
  });
  let next = runs.length;
  for (let i = 0; i < n; i++) if (r[i] < 0) r[i] = next++; // lights outside every run each stand alone (never bridged)
  return r;
}

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
function ransac(rand, n, minimal, fit, err, thr, iters, ro = {}) {
  let best = null;
  const pool = [];
  const count = (model, t) => {
    const inl = [];
    let sc = 0;
    for (let i = 0; i < n; i++) {
      const e = err(model, i);
      if (e <= t) inl.push(i);
      if (ro.msac && t === thr) sc += Math.min(e, thr) / thr; // truncated cost over all points: outliers cost 1, inliers by how well they fit
    }
    return { inl, sc };
  };
  for (let it = 0; it < iters; it++) {
    let model = fit(sample(rand, n, minimal));
    if (!model) continue;
    let c = count(model, thr);
    // local optimisation: refit on everything fairly close, tightening step by step (only for samples that already look promising)
    const promising = !best || c.inl.length >= 0.55 * best.inl.length || ro.keep > 1 && c.inl.length >= 0.4 * best.inl.length;
    for (const f of promising ? [6, 3, 1.8, 1] : []) {
      const wide = count(model, thr * f).inl;
      if (wide.length < minimal + 2) break;
      const m2 = fit(wide);
      if (!m2) break;
      const c2 = count(m2, thr);
      if (ro.msac ? c2.sc <= c.sc : c2.inl.length >= c.inl.length) (model = m2), (c = c2);
    }
    const q = { model, inl: c.inl, cost: c.sc };
    const key = (x) => (ro.msac ? -x.cost : x.inl.length);
    if (!best || key(q) > key(best)) best = q;
    if (ro.keep > 1) {
      const set = new Set(c.inl);
      const dup = pool.find((o) => {
        let both = 0;
        for (const i of o.inl) if (set.has(i)) both++;
        return both > 0.8 * Math.max(o.inl.length, c.inl.length);
      });
      if (dup) {
        if (key(q) > key(dup)) Object.assign(dup, q);
      } else pool.push(q);
    }
    if (best.inl.length >= 0.95 * n && it >= 10) break;
  }
  if (best && ro.keep > 1) best.pool = pool.sort((p, q) => (ro.msac ? p.cost - q.cost : q.inl.length - p.inl.length)).slice(0, ro.keep);
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

// signed Sampson distance (bearing units) of a match under E
function sampsonSigned(E, a, b) {
  const Ea = mv(E, a), Eb = mtv(E, b);
  return dot(b, Ea) / Math.sqrt(Ea[0] * Ea[0] + Ea[1] * Ea[1] + Eb[0] * Eb[0] + Eb[1] * Eb[1] + 1e-30);
}
const skew = (t) => [0, -t[2], t[1], t[2], 0, -t[0], -t[1], t[0], 0];
// Polish a relative pose (R, unit t) against all matches: 5 parameters (a rotation and a direction), Cauchy-weighted Sampson error.
// A pose from a 8-point fit has 7 free parameters and soaks up noise; this keeps it a genuine camera move. Returns {R, t, cost, inl} where
// cost counts every match as min(error^2 / tau^2, 1), so a pose that fits more matches more closely costs less.
function refinePose(R0, t0, A, B, tau, iters = 14) {
  let R = R0, t = unit(t0);
  const N = A.length;
  const basis = (tv) => {
    const h = Math.abs(tv[0]) < 0.8 ? [1, 0, 0] : [0, 1, 0];
    const u1 = unit(cross(tv, h)), u2 = cross(tv, u1);
    return [u1, u2];
  };
  const resid = (Rm, tv) => {
    const E = mm(skew(tv), Rm);
    return A.map((a, k) => sampsonSigned(E, a, B[k]));
  };
  const robust = (r) => {
    let c = 0;
    for (const x of r) c += Math.min((x * x) / (tau * tau), 1);
    return c;
  };
  let r = resid(R, t), cost = robust(r), lambda = 1e-2;
  const eps = 1e-5;
  for (let it = 0; it < iters; it++) {
    const [u1, u2] = basis(t);
    const J = [];
    const params = [
      (d) => [mm(rodrigues([d, 0, 0]), R), t],
      (d) => [mm(rodrigues([0, d, 0]), R), t],
      (d) => [mm(rodrigues([0, 0, d]), R), t],
      (d) => [R, unit([t[0] + d * u1[0], t[1] + d * u1[1], t[2] + d * u1[2]])],
      (d) => [R, unit([t[0] + d * u2[0], t[1] + d * u2[1], t[2] + d * u2[2]])],
    ];
    for (const f of params) {
      const [Rp, tp] = f(eps);
      const rp = resid(Rp, tp);
      J.push(rp.map((x, k) => (x - r[k]) / eps));
    }
    const w = r.map((x) => 1 / (1 + (x / tau) ** 2)); // Cauchy weights: a wrong match hardly counts
    const H = Array.from({ length: 5 }, () => new Float64Array(5)), g = new Float64Array(5);
    for (let k = 0; k < N; k++) {
      for (let i = 0; i < 5; i++) {
        g[i] -= J[i][k] * r[k] * w[k];
        for (let j = 0; j < 5; j++) H[i][j] += J[i][k] * J[j][k] * w[k];
      }
    }
    let improved = false;
    for (let attempt = 0; attempt < 6 && !improved; attempt++) {
      const S = H.map((row, i) => {
        const q = Float64Array.from(row);
        q[i] += lambda * Math.max(row[i], 1e-12);
        return q;
      });
      const d = solveLin(S, Float64Array.from(g));
      if (!d) {
        lambda *= 10;
        continue;
      }
      const Rn = mm(rodrigues([d[0], d[1], d[2]]), R);
      const tn = unit([t[0] + d[3] * u1[0] + d[4] * u2[0], t[1] + d[3] * u1[1] + d[4] * u2[1], t[2] + d[3] * u1[2] + d[4] * u2[2]]);
      const rn = resid(Rn, tn);
      // compare with the smooth robust cost (the truncated one is flat for outliers and would stall)
      const sm = (q) => q.reduce((acc, x) => acc + Math.log(1 + (x / tau) ** 2), 0);
      if (sm(rn) < sm(r)) {
        R = Rn;
        t = tn;
        r = rn;
        lambda = Math.max(lambda / 3, 1e-6);
        improved = true;
      } else lambda *= 4;
    }
    if (!improved) break;
  }
  cost = robust(r);
  return { R, t, cost, inl: r.filter((x) => Math.abs(x) < tau).length };
}

// initial candidate poses for views a, b: [{R, t, planar}] best first
function initialPoses(va, vb, shared, F, rand, tolPx, glow = false, fast = false) {
  const x1 = new Map(), x2 = new Map();
  for (const i of shared) {
    x1.set(i, bearing(va.pts.get(i), F));
    x2.set(i, bearing(vb.pts.get(i), F));
  }
  const idxs = shared, N = idxs.length;
  const A = idxs.map((i) => x1.get(i)), B = idxs.map((i) => x2.get(i));
  const thr = (tolPx / (Math.max(va.m, vb.m) * F)) ** 2;
  const out = [];
  const keep = glow && !fast ? 3 : 1;
  // essential matrix
  const eBest = N >= 8 ? ransac(rand, N, 8, (s) => {
    const a = s.map((k) => A[k]), b = s.map((k) => B[k]);
    return spreadOK(a) && spreadOK(b) ? essentialFrom(a, b) : null;
  }, (E, k) => sampson(E, A[k], B[k]), thr, fast ? 150 : glow ? 500 : 400, { msac: glow, keep }) : null;
  const hBest = N >= 4 ? ransac(rand, N, 4, (s) => {
    const a = s.map((k) => A[k]), b = s.map((k) => B[k]);
    return spreadOK(a, 0.08) && spreadOK(b, 0.08) ? homographyFrom(a, b) : null;
  }, (H, k) => transferErr(H, A[k], B[k]), thr * 1.5, fast ? 80 : glow ? 300 : 300, { msac: glow, keep: glow && !fast ? 2 : 1 }) : null;
  // If one plane explains about as many matches as the general model, the general (essential matrix) answer is unreliable: use the plane.
  // If the plane explains a good share, offer both and let the full solve decide.
  const eN = eBest ? eBest.inl.length : 0, hN = hBest ? hBest.inl.length : 0;
  const planar = hN >= 4 && (hN >= 0.9 * N || hN >= 0.88 * eN);
  const mixed = hN >= 6 && hN >= 0.55 * eN;
  const dedupe = (c) => !out.some((o) => angleBetween(o.R, c.R) < 1 && norm(sub(o.t, c.t)) < 0.05);
  if (eBest && eBest.inl.length >= 8 && !planar) {
    for (const hyp of eBest.pool || [eBest]) {
      if (hyp.inl.length < 8) continue;
      const E = essentialFrom(hyp.inl.map((k) => A[k]), hyp.inl.map((k) => B[k]));
      const cands = decomposeE(E).map((c) => ({ ...c, ...scorePose(c.R, c.t, x1, x2, hyp.inl.map((k) => idxs[k])), planar: false }));
      cands.sort((p, q) => q.good - p.good || p.err - q.err);
      if (cands[0].good >= 6 && dedupe(cands[0])) out.push(cands[0]);
    }
  }
  if (hBest && hBest.inl.length >= 4 && (planar || mixed || !out.length)) {
    for (const hyp of hBest.pool || [hBest]) {
      if (hyp.inl.length < 4) continue;
      const H = homographyFrom(hyp.inl.map((k) => A[k]), hyp.inl.map((k) => B[k]));
      let sign = 0;
      for (const k of hyp.inl) sign += dot(B[k], mv(H, A[k]));
      const Hs = sign < 0 ? H.map((x) => -x) : H;
      const inl = hyp.inl.map((k) => idxs[k]);
      const cands = decomposeH(Hs).map((c) => ({ ...c, ...scorePose(c.R, c.t, x1, x2, inl), planar: true }));
      cands.sort((p, q) => q.good - p.good || p.err - q.err);
      const top = cands[0] ? cands[0].good : 0;
      for (const c of cands) if (c.good >= Math.max(4, 0.8 * top) && dedupe(c)) out.push(c);
    }
  }
  if (glow && out.length) {
    // polish every hypothesis against all the matches with the same 5-parameter model, rank them by how well they explain the matches
    const tau = tolPx / (Math.max(va.m, vb.m) * F);
    const pol = [];
    for (const c of out) {
      const q = refinePose(c.R, c.t, A, B, tau);
      const sc = scorePose(q.R, q.t, x1, x2, idxs);
      if (sc.good < 5) continue;
      if (!pol.some((o) => angleBetween(o.R, q.R) < 2 && norm(sub(o.t, q.t)) < 0.1)) pol.push({ ...c, R: q.R, t: q.t, good: sc.good, err: sc.err, cost: q.cost, einl: q.inl });
    }
    pol.sort((p, q) => p.cost - q.cost);
    return pol.slice(0, 6);
  }
  return out.slice(0, 4);
}

// the focal length is only a guess: try a few around it and keep the one that explains the most matches
function initialPosesF(va, vb, shared, F0, rand, tolPx, glow = false) {
  let best = null;
  const tryF = (F) => {
    const c = initialPoses(va, vb, shared, F, rand, tolPx);
    if (!c.length) return;
    c.forEach((x) => (x.F = F));
    if (!best || c[0].good > best.score) best = { score: c[0].good, c, F };
  };
  tryF(F0);
  if (best && best.score >= (glow ? 0.7 : 0.85) * shared.length) return best.c;
  if (glow && best) return best.c; // the guess is as good as any: bundle adjustment will find the focal length once there are three views
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
  const dlt = (s) => (lineLike3(s.map((k) => P[k].X)) ? null : poseDLT(s.map((k) => P[k])));
  const plane = (s) => (lineLike3(s.map((k) => P[k].X)) ? null : posePlane(s.map((k) => P[k])));
  const tries = flat ? [[4, plane], [6, dlt]] : [[6, dlt], [4, plane]];
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

// Second way to place a view: its relative pose to one already placed view comes from the matches between those two (essential matrix, or the
// plane when the matches are flat; many more matches than the few already-triangulated lights it sees), and only the length of the move is
// left to the known 3D points (a 1-D search). Used when 3D-2D (PnP) has too few points or too many outliers, which is common with sparse glow spots.
function registerRel(st, views, n, v, rand, tol, glow) {
  const known = [];
  for (const [i, p] of views[v].pts) if (st.has[i]) known.push({ X: [st.X[3 * i], st.X[3 * i + 1], st.X[3 * i + 2]], p });
  const regs = [...st.reg].map((r) => {
    const shared = [];
    for (const i of views[v].pts.keys()) if (views[r].pts.has(i)) shared.push(i);
    return { r, shared };
  }).filter((q) => q.shared.length >= 8).sort((a, b) => b.shared.length - a.shared.length);
  if (!regs.length || (known.length < 3 && regs.length < 2)) return null;
  const thrPx = 2 * tol, mV = views[v].m;
  // bearings of the matches with every placed view, for the epipolar check of a candidate position (all matches count, not only the
  // few already triangulated)
  for (const q of regs) {
    q.a = q.shared.map((i) => bearing(views[q.r].pts.get(i), st.F));
    q.b = q.shared.map((i) => bearing(views[v].pts.get(i), st.F));
    q.thr = (thrPx / (Math.max(views[q.r].m, mV) * st.F)) ** 2;
  }
  let best = null;
  for (const { r, shared } of regs.slice(0, 3)) {
    const cands = initialPoses(views[r], views[v], shared, st.F, rand, tol * 1.5, glow, true);
    for (const c of cands) {
      const Rv = mm(c.R, st.R[r]), t0 = mv(c.R, st.t[r]);
      const evalL = (lam) => {
        const tv = [t0[0] + lam * c.t[0], t0[1] + lam * c.t[1], t0[2] + lam * c.t[2]];
        let sc = 0, inl = 0, einl = 0, esc = 0;
        for (const k of known) {
          const pr = project(Rv, tv, st.F, k.X);
          if (!pr) continue;
          const e = Math.hypot(pr[0] - k.p[0], pr[1] - k.p[1]) * mV;
          if (e < thrPx) (sc += 1 - (e / thrPx) ** 2), inl++;
        }
        for (const q of regs) {
          if (q.r === r) continue;
          const Rr = mm(Rv, mt(st.R[q.r])), tr = mv(Rr, st.t[q.r]);
          const t = [tv[0] - tr[0], tv[1] - tr[1], tv[2] - tr[2]];
          // x_v = Rr x_r + t  ->  E = [t]x Rr, and  x_v^T E x_r = 0
          const E = mm([0, -t[2], t[1], t[2], 0, -t[0], -t[1], t[0], 0], Rr);
          for (let k = 0; k < q.a.length; k++) {
            const d = sampson(E, q.a[k], q.b[k]);
            if (d < q.thr) (esc += 1 - d / q.thr), einl++;
          }
        }
        return { sc: sc + esc, inl, einl, tv };
      };
      let bl = null;
      for (let lam = 0.03; lam < 30; lam *= 1.12) {
        const q = evalL(lam);
        if (!bl || q.sc > bl.sc) bl = { ...q, lam };
      }
      if (!bl) continue;
      for (let f = 0.9; f <= 1.1; f += 0.01) {
        const q = evalL(bl.lam * f);
        if (q.sc > bl.sc) bl = { ...q, lam: bl.lam * f };
      }
      if (!best || bl.sc > best.sc) best = { R: Rv, t: bl.tv, inl: bl.inl, einl: bl.einl, sc: bl.sc, known: known.length, others: regs.length - 1 };
    }
  }
  if (!best) return null;
  const okKnown = best.inl >= Math.max(3, 0.4 * best.known);
  const okEpi = best.others >= 1 && best.einl >= Math.max(8, 0.4 * regs.slice(1).reduce((x, q) => x + q.shared.length, 0));
  if (!(okKnown || okEpi)) return null;
  best.inl = Math.max(best.inl, 6);
  return best;
}

// ---- bundle adjustment ------------------------------------------------------------------------------------------------------------
// st: {F, R[v], t[v], reg:Set, ref, X (Float64Array 3n), has (Uint8Array n), bad:Set(v*n+i)}; views[v].pts normalised obs.
function bundleAdjust(st, views, n, o = {}) {
  const freeViews = [...st.reg].filter((v) => v !== st.ref);
  const off = new Map(freeViews.map((v, k) => [v, 6 * k]));
  const freeF = o.freeF !== false && st.reg.size >= 3; // two views cannot tell the focal length from the scene depth
  const sigN = st.sigPx ? st.sigPx / st.mRef : 0.0005; // pixel noise in normalised units
  const wF = (sigN / ((st.fSigma ?? 0.3) * st.F0)) ** 2; // a pull towards the guess so it can't run away (stronger when the pixels are noisy)
  const Fmin = st.F0 * (st.fRange ? st.fRange[0] : 0.58), Fmax = st.F0 * (st.fRange ? st.fRange[1] : 2.05);
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
  const delta = o.huber ?? (st.huberPx ?? 2.9) / (st.mRef || 1920); // normalised units (about 3 px on a 1920 picture)
  const eps = 1e-6;
  const dR = [0, 1, 2].map((j) => rodrigues([j === 0 ? eps : 0, j === 1 ? eps : 0, j === 2 ? eps : 0]));
  const cost = (R, t, F, X) => {
    let c = (freeF ? 0.5 * wF * (F - st.F0) ** 2 : 0) + priorCost(X) + gravityCost(R);
    for (const q of live) {
      const pr = project(R[q.v], t[q.v], F, [X[3 * q.i], X[3 * q.i + 1], X[3 * q.i + 2]]);
      if (!pr) return Infinity;
      const r = Math.hypot(q.u - pr[0], q.w - pr[1]);
      c += r <= delta ? 0.5 * r * r : delta * (r - 0.5 * delta);
    }
    return c;
  };
  // String prior (soft): a light sits near the straight line between its two neighbours on the same run, loosely (a fraction of their
  // spacing, so corners cost little and a strip that bends is allowed). Neighbours are held while a point moves, then re-read each step.
  const kappa = o.kappa ?? st.kappa ?? 0.4;
  const priorOn = !!st.runOf && kappa > 0 && o.prior !== false;
  let tri = [];
  const buildTri = () => {
    tri = [];
    if (!priorOn) return;
    const ids = [...slot.keys()].sort((a, b) => a - b);
    for (let k = 1; k + 1 < ids.length; k++) {
      const a = ids[k - 1], i = ids[k], c = ids[k + 1];
      if (st.runOf[a] !== st.runOf[i] || st.runOf[c] !== st.runOf[i] || c - a > (st.maxGap ?? 40)) continue;
      const f = (i - a) / (c - a);
      const prd = [0, 1, 2].map((m) => (1 - f) * st.X[3 * a + m] + f * st.X[3 * c + m]);
      const chord = Math.hypot(st.X[3 * c] - st.X[3 * a], st.X[3 * c + 1] - st.X[3 * a + 1], st.X[3 * c + 2] - st.X[3 * a + 2]);
      const sg = Math.max(kappa * chord, 1e-6);
      const d = Math.hypot(st.X[3 * i] - prd[0], st.X[3 * i + 1] - prd[1], st.X[3 * i + 2] - prd[2]);
      const hub = d > 3 * sg ? (3 * sg) / d : 1; // a corner or a wrong match does not drag its neighbours
      tri.push({ a, i, c, f, w: (sigN / sg) ** 2 * hub * (st.priorGain ?? 1) });
    }
  };
  const priorCost = (X) => {
    let c = 0;
    for (const q of tri) {
      let d2 = 0;
      for (let m = 0; m < 3; m++) d2 += (X[3 * q.i + m] - ((1 - q.f) * X[3 * q.a + m] + q.f * X[3 * q.c + m])) ** 2;
      c += 0.5 * q.w * d2;
    }
    return c;
  };
  // Gravity prior: with the phones' angles, the same world "up" must come out of every camera. The common up direction (in the first
  // camera's axes) is re-read each step; the rotation of each free camera is pulled so that it matches the up that camera measured.
  const gDirs = [];
  if (st.gravity && o.gravity !== false) for (const v of st.reg) if (st.gravity[v]) gDirs.push(v);
  const gOn = gDirs.length >= 2;
  const wG = (sigN / (st.F0 * (st.gravSigma ?? 0.07))) ** 2;
  let gUp = null;
  const gravityRead = () => {
    gUp = null;
    if (!gOn) return;
    const sum = [0, 0, 0];
    for (const v of gDirs) {
      const u = mtv(st.R[v], st.gravity[v]);
      for (let m = 0; m < 3; m++) sum[m] += u[m];
    }
    if (norm(sum) < 0.3 * gDirs.length) return;
    gUp = unit(sum);
  };
  const gravityCost = (R) => {
    if (!gUp) return 0;
    let c = 0;
    for (const v of gDirs) {
      const r = sub(mv(R[v], gUp), st.gravity[v]);
      c += 0.5 * wG * dot(r, r);
    }
    return c;
  };
  let lambda = o.lambda ?? 1e-3;
  let cur = 0;
  for (let it = 0; it < (o.iters ?? 15); it++) {
    buildTri();
    gravityRead();
    cur = cost(st.R, st.t, st.F, st.X);
    const U = Array.from({ length: P }, () => new Float64Array(P)), eC = new Float64Array(P);
    const Vp = new Float64Array(9 * NP), eP = new Float64Array(3 * NP), Wp = new Float64Array(P * 3 * NP);
    for (const q of live) {
      const R = st.R[q.v], t = st.t[q.v], X = [st.X[3 * q.i], st.X[3 * q.i + 1], st.X[3 * q.i + 2]];
      const base = project(R, t, st.F, X);
      if (!base) continue;
      const e0 = q.u - base[0], e1 = q.w - base[1], r = Math.hypot(e0, e1);
      const sw = Math.sqrt(r <= delta ? 1 : delta / r);
      const o6 = off.get(q.v);
      const cc = [];
      const Jc = [[], []];
      if (o6 !== undefined) {
        for (let j = 0; j < 3; j++) {
          const p = project(mm(dR[j], R), t, st.F, X) || base;
          cc.push(o6 + j);
          Jc[0].push(((p[0] - base[0]) / eps) * sw);
          Jc[1].push(((p[1] - base[1]) / eps) * sw);
        }
        for (let j = 0; j < 3; j++) {
          const tp = t.slice();
          tp[j] += eps;
          const p = project(R, tp, st.F, X) || base;
          cc.push(o6 + 3 + j);
          Jc[0].push(((p[0] - base[0]) / eps) * sw);
          Jc[1].push(((p[1] - base[1]) / eps) * sw);
        }
      }
      if (freeF) {
        const p = project(R, t, st.F + eps, X) || base;
        cc.push(fcol);
        Jc[0].push(((p[0] - base[0]) / eps) * sw);
        Jc[1].push(((p[1] - base[1]) / eps) * sw);
      }
      const Jp = [[0, 0, 0], [0, 0, 0]];
      for (let j = 0; j < 3; j++) {
        const Xp = X.slice();
        Xp[j] += eps;
        const p = project(R, t, st.F, Xp) || base;
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
    for (const q of tri) {
      const s = slot.get(q.i);
      for (let m = 0; m < 3; m++) {
        Vp[9 * s + 4 * m] += q.w;
        eP[3 * s + m] -= q.w * (st.X[3 * q.i + m] - ((1 - q.f) * st.X[3 * q.a + m] + q.f * st.X[3 * q.c + m]));
      }
    }
    if (gUp) {
      for (const v of gDirs) {
        const o6 = off.get(v);
        if (o6 === undefined) continue;
        const Rg = mv(st.R[v], gUp), r = sub(st.gravity[v], Rg); // target - predicted
        // d(Rg)/d(delta) for R' = exp(delta) R: delta x Rg  ->  columns -[Rg]x
        const Jm = [0, Rg[2], -Rg[1], -Rg[2], 0, Rg[0], Rg[1], -Rg[0], 0]; // 3x3 rows: derivative of Rg_row wrt delta_col (delta x Rg = -[Rg]x delta)
        for (let a = 0; a < 3; a++) {
          for (let row = 0; row < 3; row++) eC[o6 + a] += Jm[row * 3 + a] * r[row] * wG;
          for (let b = 0; b < 3; b++) {
            let acc = 0;
            for (let row = 0; row < 3; row++) acc += Jm[row * 3 + a] * Jm[row * 3 + b];
            U[o6 + a][o6 + b] += acc * wG;
          }
        }
      }
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
      if (freeF) nF = Math.max(Fmin, Math.min(Fmax, st.F + dc[fcol]));
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

// noise (pixels, per axis) from the residuals: the median of a 2D Gaussian residual length is 1.18 sigma
function noiseOf(rep) {
  const errs = [...rep.per.values()].filter((e) => e < 1e8);
  return errs.length ? median(errs) / 1.18 : 0;
}
function dropOutliers(st, views, n, rep, tolPx) {
  const errs = [...rep.per.values()];
  const med = median(errs);
  const cut = Math.max(tolPx, (st.cutK ?? 4) * med);
  st.cutNow = cut;
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
  const mRef = median(views.map((v) => v.m)) || 1920;
  const glow = !!o.glow;
  const it = (k) => Math.max(3, Math.round(k * (o.light ? 0.4 : 1))); // a light pass (to rank hypotheses cheaply) runs fewer iterations
  const st = {
    mRef, huberPx: glow ? o.tolPx * 0.5 : o.tolPx * 0.97, sigPx: glow ? o.tolPx / 3 : 0, cutK: glow ? 2.8 : 4, F: F0, F0,
    R: new Array(V).fill(null), t: new Array(V).fill(null), reg: new Set([a, b]), ref: a, X: new Float64Array(3 * n), has: new Uint8Array(n), bad: new Set(),
    runOf: o.runOf || null, gravity: o.gravity || null, fRange: glow ? [0.72, 1.4] : null, fSigma: glow ? 0.15 : 0.3,
    kappa: o.kappa, maxGap: o.maxGap, priorGain: o.priorGain, gravSigma: o.gravSigma,
  };
  st.R[a] = I3();
  st.t[a] = [0, 0, 0];
  st.R[b] = pose.R;
  st.t[b] = pose.t;
  const tol = o.tolPx;
  const minAng = glow ? 1.5 : 0.5;
  // the focal length is only a guess at first, so be generous; bundle adjustment then fixes it and the tight check follows
  triangulateNew(st, views, n, tol * (glow ? 3 : 8), minAng);
  if (st.has.reduce((x, y) => x + y, 0) < 6) return null;
  const retune = (rep) => {
    // learn the noise level from the residuals so the pixel weights and the rejection limit follow it (glow centres wander by 5-15 px)
    if (!glow || st.reg.size < 3) return; // two views fit anything along the epipolar lines: their residuals understate the noise
    const sg = noiseOf(rep);
    if (sg > 0) {
      st.sigPx = Math.min(Math.max(sg, tol / 4), tol);
      st.huberPx = Math.min(Math.max(1.3 * sg, tol / 3), tol);
    }
  };
  let rep = bundleAdjust(st, views, n, { iters: it(15), huber: ((glow ? 1.2 : 2.7) * tol) / st.mRef, prior: false });
  retune(rep);
  rep = bundleAdjust(st, views, n, { iters: it(10) });
  retune(rep);
  dropOutliers(st, views, n, rep, tol * 2);
  triangulateNew(st, views, n, tol * 2, minAng);
  rep = bundleAdjust(st, views, n, { iters: it(10) });
  retune(rep);
  dropOutliers(st, views, n, rep, tol);
  rep = bundleAdjust(st, views, n, { iters: it(10) });
  const failed = new Set();
  const why = new Map();
  for (;;) {
    // the unregistered view that sees the most triangulated points (or, failing that, shares the most lights with a placed view)
    let bestV = -1, bestN = 0, bestS = 0;
    for (let v = 0; v < V; v++) {
      if (st.reg.has(v) || failed.has(v)) continue;
      let c = 0, sh = 0;
      for (const i of views[v].pts.keys()) if (st.has[i]) c++;
      for (const r of st.reg) {
        let q = 0;
        for (const i of views[v].pts.keys()) if (views[r].pts.has(i)) q++;
        sh = Math.max(sh, q);
      }
      if (c > bestN || (c === bestN && sh > bestS)) (bestN = c), (bestS = sh), (bestV = v);
    }
    if (bestV < 0) break;
    let reg = null, P = [];
    for (const [i, p] of views[bestV].pts) if (st.has[i]) P.push({ X: [st.X[3 * i], st.X[3 * i + 1], st.X[3 * i + 2]], b: bearing(p, st.F) });
    if (bestN >= 6) {
      const thr = ((glow ? 2 : 3) * Math.max(tol, glow ? st.cutNow || tol : tol) / (views[bestV].m * st.F)) ** 2;
      reg = registerPnP(P, st.F, rand, thr * 1.0, null);
      if (o.trace) o.trace({ pnp: bestV, P: P.length, inl: reg ? reg.inl : -1, reg: st.reg.size });
      if (reg && reg.inl < Math.max(6, 0.3 * P.length)) reg = null;
    }
    if (!reg) {
      reg = registerRel(st, views, n, bestV, rand, glow ? Math.max(tol, st.cutNow || tol) : tol, glow);
      if (o.trace) o.trace({ rel: bestV, P: P.length, inl: reg ? reg.inl : -1, reg: st.reg.size });
    }
    if (!reg) {
      failed.add(bestV); // cannot place this one: leave it out and try the others
      why.set(bestV, { seen: P.length, inl: 0 });
      continue;
    }
    failed.clear();
    why.delete(bestV);
    st.R[bestV] = reg.R;
    st.t[bestV] = reg.t;
    st.reg.add(bestV);
    triangulateNew(st, views, n, tol * 2, glow ? 2 : 1.0);
    rep = bundleAdjust(st, views, n, { iters: it(10) });
    retune(rep);
    dropOutliers(st, views, n, rep, tol);
    rep = bundleAdjust(st, views, n, { iters: it(8) });
  }
  triangulateNew(st, views, n, tol * 2, glow ? 2 : 1.0);
  rep = bundleAdjust(st, views, n, { iters: it(20) });
  retune(rep);
  dropOutliers(st, views, n, rep, tol);
  triangulateNew(st, views, n, tol, glow ? 2 : 1.0);
  rep = bundleAdjust(st, views, n, { iters: it(25), lambda: 1e-4 });
  retune(rep);
  const dropped = dropOutliers(st, views, n, rep, tol);
  if (dropped) rep = bundleAdjust(st, views, n, { iters: it(10), lambda: 1e-4 });
  // a score to compare candidate solutions: registered views first, then how many observations fit (each counts less the worse it fits)
  const cut = st.cutNow || tol;
  let score = 0;
  for (const e of rep.per.values()) if (e < cut) score += 1 - (e / cut) ** 2;
  return { st, rep, score, why, noisePx: noiseOf(rep) };
}

// ---- output ---------------------------------------------------------------------------------------------------------------------------------
// rotation that carries unit vector a onto b
function rotateOnto(a, b) {
  const c = cross(a, b), s = norm(c), d = dot(a, b);
  if (s < 1e-9) return d > 0 ? I3() : rodrigues([Math.PI, 0, 0]);
  return rodrigues(c.map((q) => (q / s) * Math.atan2(s, d)));
}

// Solve. views as in the header; opts:
//   n (LED count), fovDeg (start guess, 65), tolPx (inlier floor in pixels; 3 for a point scan, 18 for glow views), seed,
//   glow (default: any view has glow: true) - sparse glow centres: wider tolerances that follow the measured noise, stricter parallax,
//   runs [[start, count], ...] - the strings; the string prior, the pruning and the filling of unseen lights never cross a run boundary,
//   stringPrior (default true when runs are given), stringKappa (looseness, a fraction of the neighbours' spacing, 0.4),
//   gravity (default true when 2+ views have orientation), maxBridge (legacy gap limit when there are no runs, 60), trace (debug callback).
// Returns {ok, error?, warnings: [string], pos (Float32Array 3n, 0-1 per axis, y up, z towards the first camera), est (Uint8Array n: 1 = placed
// by string order or a single view), count (Uint8Array n: views that saw each LED), used (Uint8Array n: views that went into the 3D point),
// err (Float32Array n: pixel rms of the light's observations, 0 if not triangulated), views: [{ok, rms, count, used, shared, center, forward,
// R, error?}], rms, noisePx, fovDeg, found, pruned, leveled, glow}
export function solveMultiView(viewsIn, opts = {}) {
  let n = opts.n ?? 0;
  if (!n) for (const v of viewsIn) for (const k of v.points instanceof Map ? v.points.keys() : Object.keys(v.points || {})) n = Math.max(n, Number(k) + 1);
  const fail = (error, per) => ({ ok: false, error, warnings: [], n, views: viewsIn.map((v, k) => ({ ok: false, error: per?.[k] || error })), pos: new Float32Array(3 * n), est: new Uint8Array(n).fill(1), count: coverage(viewsIn, n).seen, found: 0 });
  if (viewsIn.length < 2) return fail("Need at least two views.");
  const views = viewsIn.map((v) => prepView(v, n));
  const V = views.length;
  const glow = opts.glow ?? viewsIn.some((v) => v.glow);
  const fov = opts.fovDeg ?? views.find((v) => v.fovDeg)?.fovDeg ?? 65;
  const F0 = fovToF(fov);
  const tolPx = opts.tolPx ?? (glow ? 18 : 3);
  const rand = rng(opts.seed ?? 12345);
  const runs = Array.isArray(opts.runs) ? opts.runs.filter((q) => Array.isArray(q) && q[1] > 0) : [];
  const runOf = runIndex(n, runs);
  const useRuns = runs.length > 0;
  const prior = useRuns && opts.stringPrior !== false;
  const gravity = views.map((v) => (v.Rcw ? mtv(v.Rcw, [0, 0, 1]) : null));
  const useGravity = opts.gravity !== false && gravity.filter(Boolean).length >= 2;
  const warnings = [];
  const label = (v) => `View ${v + 1}`;
  // pairs by shared points
  const pairs = [];
  const minShared = opts.minShared ?? 8;
  const sharedWith = Array.from({ length: V }, () => 0);
  for (let a = 0; a < V; a++) {
    for (let b = a + 1; b < V; b++) {
      const shared = [];
      for (const i of views[a].pts.keys()) if (views[b].pts.has(i)) shared.push(i);
      sharedWith[a] = Math.max(sharedWith[a], shared.length);
      sharedWith[b] = Math.max(sharedWith[b], shared.length);
      if (shared.length >= minShared) pairs.push({ a, b, shared });
    }
  }
  for (let v = 0; v < V; v++) if (views[v].pts.size < 6) warnings.push(`${label(v)} found only ${views[v].pts.size} light${views[v].pts.size === 1 ? "" : "s"}; scan it again closer or with the lights brighter.`);
  if (!pairs.length) {
    const mx = Math.max(...sharedWith);
    return fail(`No two views share enough lights: the best pair has ${mx} in common and at least ${minShared} are needed. Scan again from angles that see the same strips, a little closer together (about 30 to 45 degrees apart).`, viewsIn.map((_, v) => `${label(v)} shares at most ${sharedWith[v]} lights with any other view (needs ${minShared}).`));
  }
  pairs.sort((p, q) => q.shared.length - p.shared.length);
  let best = null;
  const failures = [];
  const priorOf = (r, pr) => {
    if (!(views[pr.a].Rcw && views[pr.b].Rcw)) return 0;
    return angleBetween(mm(mt(views[pr.b].Rcw), views[pr.a].Rcw), r.st.R[pr.b]);
  };
  const better = (r, x) => {
    if (!x) return true;
    if (r.st.reg.size !== x.st.reg.size) return r.st.reg.size > x.st.reg.size;
    // flat scenes have two look-alike answers: when the fits are about as good, the one agreeing with the phone's angles wins
    if (Math.abs(r.score - x.score) < 0.06 * Math.max(r.score, x.score, 1) && r.prior < x.prior - 10) return true;
    return r.score > x.score;
  };
  const consider = (pr, c, Fuse, light = false) => {
    const r = reconstruct(views, n, pr.a, pr.b, c, Fuse, rand, { light, trace: opts.trace ? opts.trace : null, tolPx, glow, runOf: prior ? runOf : null, gravity: useGravity ? gravity : null, kappa: opts.stringKappa, maxGap: opts.maxGap, priorGain: opts.priorGain, gravSigma: opts.gravSigma });
    if (opts.trace) opts.trace({ pair: [pr.a, pr.b], shared: pr.shared.length, F: Fuse, planar: c.planar, good: c.good, reg: r ? r.st.reg.size : 0, rms: r ? r.rep.rms : null, score: r ? r.score : 0, found: r ? r.st.has.reduce((x, y) => x + y, 0) : 0 });
    if (!r) return null;
    if (opts.onCandidate) opts.onCandidate(r, Fuse);
    r.pair = pr;
    r.prior = priorOf(r, pr);
    r.cand = c;
    r.Fuse = Fuse;
    if (!light && better(r, best)) best = r;
    return r;
  };
  const good = () => best && best.st.reg.size === V && best.rep.rms < (glow ? 0.5 : 1) * tolPx;
  let collinearPairs = 0;
  if (opts.forcePose) {
    const fp = opts.forcePose, pr = pairs.find((q) => q.a === fp.a && q.b === fp.b) || { a: fp.a, b: fp.b, shared: [] };
    consider(pr, { R: fp.R, t: fp.t, planar: false, good: 0 }, fp.F ?? F0);
  } else if (!glow) {
    for (const pr of pairs.slice(0, 3)) {
      const x = pr.shared.map((i) => views[pr.a].pts.get(i));
      if (!spreadOK(x, 0.03)) {
        collinearPairs++;
        failures.push(`${label(pr.a)} and ${label(pr.b)} share ${pr.shared.length} lights, but they all lie along one line, which cannot fix where the camera moved.`);
        continue;
      }
      const cands = initialPosesF(views[pr.a], views[pr.b], pr.shared, F0, rand, tolPx * 1.5, false);
      for (const c of cands) consider(pr, c, c.F ?? F0);
      if (!cands.length) failures.push(`${label(pr.a)} and ${label(pr.b)} share ${pr.shared.length} lights but no single camera move explains more than a few of them (the matches may be noisy or mixed up).`);
      if (good()) break;
    }
  } else {
    // Glow views: few, noisy points. The pairs with the most matches often have a tiny baseline (neighbouring angles), which makes the relative
    // pose shaky, so pairs are ranked by matches times how far the lights move between the two pictures. Several pose hypotheses per pair and
    // three guesses of the focal length are tried through the whole solve; the best-fitting one wins.
    for (const pr of pairs) {
      const d = [];
      for (const i of pr.shared) {
        const p = views[pr.a].pts.get(i), q = views[pr.b].pts.get(i);
        d.push(Math.hypot(p[0] - q[0], p[1] - q[1]));
      }
      pr.rank = pr.shared.length * (0.35 + Math.min(1, median(d) / 0.12));
    }
    const order = [...pairs].sort((p, q) => q.rank - p.rank).slice(0, 4);
    // a tournament: every (pair, hypothesis) gets a quick light solve; only the best few get the full one
    const tournament = (Fg, pairList, perPair, keep) => {
      const runs = [];
      for (const pr of pairList) {
        const x = pr.shared.map((i) => views[pr.a].pts.get(i));
        if (!spreadOK(x, 0.03)) {
          if (Fg === F0) {
            collinearPairs++;
            failures.push(`${label(pr.a)} and ${label(pr.b)} share ${pr.shared.length} lights, but they all lie along one line, which cannot fix where the camera moved.`);
          }
          continue;
        }
        const cands = initialPoses(views[pr.a], views[pr.b], pr.shared, Fg, rand, tolPx * 1.5, true);
        if (!cands.length && Fg === F0) failures.push(`${label(pr.a)} and ${label(pr.b)} share ${pr.shared.length} lights but no single camera move explains more than a few of them (the matches may be noisy or mixed up).`);
        for (const c of cands.slice(0, perPair)) {
          const r = consider(pr, c, Fg, true);
          if (r) runs.push({ pr, c, r });
        }
      }
      runs.sort((p, q) => q.r.st.reg.size - p.r.st.reg.size || q.r.score - p.r.score);
      for (const q of runs.slice(0, keep)) consider(q.pr, q.c, Fg, false);
    };
    tournament(F0, order, 3, 3);
    if (!good()) {
      tournament(F0 * 0.87, order.slice(0, 2), 2, 1);
      tournament(F0 * 1.15, order.slice(0, 2), 2, 1);
    }
  }
  if (!best) {
    if (collinearPairs === Math.min(pairs.length, 4)) return fail("The lights the views share all lie along one straight line, which cannot fix the camera positions. Include a second strip at a different height or in a different part of the room, then scan again.");
    return fail(`Couldn't work out how the camera moved between the views. ${failures[0] ? failures[0] + " " : ""}Scan from angles that see many of the same lights (about 30 to 45 degrees apart), and keep the phone steady while it scans.`);
  }
  if (opts.onBest) opts.onBest(best);
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
  if (found0 < 8) return fail("Too few lights were seen from two angles. Scan again with more lights visible from each angle.");

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
  let pruned = 0;
  const dropPoint = (i) => {
    st.has[i] = 0;
    pruned++;
  };
  const solidList = () => {
    const a = [];
    for (let i = 0; i < n; i++) if (st.has[i]) a.push(i);
    return a;
  };
  // (1) glow: a point seen from nearly the same direction in every camera has a depth that is mostly noise; the string places it better
  if (glow) {
    const centres = new Map();
    for (const v of st.reg) centres.set(v, mtv(st.R[v], [-st.t[v][0], -st.t[v][1], -st.t[v][2]]));
    const weak = [];
    let total = 0;
    for (let i = 0; i < n; i++) {
      if (!st.has[i]) continue;
      total++;
      const X = [st.X[3 * i], st.X[3 * i + 1], st.X[3 * i + 2]];
      const dirs = [];
      for (const v of st.reg) if (views[v].pts.has(i) && !st.bad.has(v * n + i)) dirs.push(unit(sub(X, centres.get(v))));
      let mx = 0;
      for (let a = 0; a < dirs.length; a++) for (let b = a + 1; b < dirs.length; b++) mx = Math.max(mx, Math.acos(Math.max(-1, Math.min(1, dot(dirs[a], dirs[b])))) * R2D);
      if (mx < (opts.minAngle ?? 3)) weak.push(i);
    }
    if (weak.length <= 0.5 * total) for (const i of weak) dropPoint(i);
  }
  // (2) A light seen from only two views can be a wrong match that happens to sit on its epipolar line. Drop solid points that are far from
  // everything else, or far off the line their string neighbours make (the string is smooth).
  {
    const idx = solidList();
    if (idx.length >= 12) {
      const cen = [0, 1, 2].map((c) => median(idx.map((i) => pts[3 * i + c])));
      const dists = idx.map((i) => Math.hypot(pts[3 * i] - cen[0], pts[3 * i + 1] - cen[1], pts[3 * i + 2] - cen[2]));
      const far = 3 * [...dists].sort((p, q) => p - q)[Math.floor(dists.length * 0.9)];
      idx.forEach((i, k) => {
        if (dists[k] > far) dropPoint(i);
      });
      if (!useRuns) {
        const live = solidList();
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
            if (Math.sqrt(dev) > 6 * step * (q - p)) dropPoint(i);
          }
        }
      } else {
        // Runs. Two physical facts about a string: two lights k steps apart on it are at most k pitches apart (the string cannot be shorter than
        // the straight line), and a light sits near the line between its two neighbours unless it is at a corner. The worst offender is dropped,
        // then the neighbours are judged again without it. Nothing crosses a run boundary.
        const P3 = (i) => [pts[3 * i], pts[3 * i + 1], pts[3 * i + 2]];
        const gapMax = opts.maxGap ?? 40;
        const limit = opts.chordLimit ?? 1.2, speedLimit = opts.speedLimit ?? 2.5;
        for (let round = 0; round < 300; round++) {
          const live = solidList();
          // pitch: distance per index step along the strings (an upper-middle quantile of adjacent pairs, so corners and gaps do not shrink it)
          const speeds = [];
          for (let k = 0; k + 1 < live.length; k++) if (runOf[live[k]] === runOf[live[k + 1]] && live[k + 1] - live[k] <= 12) speeds.push(norm(sub(P3(live[k + 1]), P3(live[k]))) / (live[k + 1] - live[k]));
          if (speeds.length < 4) break;
          speeds.sort((p, q) => p - q);
          const pitch = speeds[Math.floor(speeds.length * 0.7)];
          let worst = -1, wr = -1e9;
          for (let k = 0; k < live.length; k++) {
            const i = live[k], p = live[k - 1], q = live[k + 1];
            const sameP = p !== undefined && runOf[p] === runOf[i], sameQ = q !== undefined && runOf[q] === runOf[i];
            const hasP = sameP && i - p <= gapMax, hasQ = sameQ && q - i <= gapMax;
            let score = 0, top = 0, agree = 0;
            for (const [has, o] of [[sameP, p], [sameQ, q]]) {
              if (!has) continue;
              const ratio = norm(sub(P3(i), P3(o))) / Math.abs(i - o) / (speedLimit * pitch);
              if (ratio > 1) (score += ratio), (top = Math.max(top, ratio));
              else agree++;
            }
            if (hasP && hasQ) {
              const f = (i - p) / (q - p), A = P3(p), B = P3(q), X = P3(i);
              const chord = norm(sub(B, A)) + 1e-9;
              const dev = norm(sub(X, [A[0] + (B[0] - A[0]) * f, A[1] + (B[1] - A[1]) * f, A[2] + (B[2] - A[2]) * f]));
              const ratio = dev / (limit * Math.max(chord, pitch * (q - p) * 0.5));
              if (ratio > 1) (score += ratio), (top = Math.max(top, ratio));
            }
            if (top > 1 && score - 1.5 * agree > wr - 1) (wr = score - 1.5 * agree + 1), (worst = i); // blame the end with no agreeing neighbour
          }
          if (worst < 0) break;
          dropPoint(worst);
        }
      }
    }
  }
  // lights seen once, or never, or pruned: a light seen in some view keeps its place in the picture and takes its depth from its string
  // neighbours (or the view's middle depth); one seen nowhere is put on the line between its string neighbours (the same run only)
  const est = new Uint8Array(n);
  const solid = solidList();
  for (const i of solid) {
    const q = mv(T, [st.X[3 * i], st.X[3 * i + 1], st.X[3 * i + 2]]);
    pts[3 * i] = q[0];
    pts[3 * i + 1] = q[1];
    pts[3 * i + 2] = q[2];
  }
  if (solid.length < 8) return fail("Too few lights could be placed reliably in 3D. Scan again from angles that see more of the same lights, and make sure the strips are in view.");
  const camZ = (v, i) => {
    const p = mv(st.R[v], [st.X[3 * i], st.X[3 * i + 1], st.X[3 * i + 2]]);
    return p[2] + st.t[v][2];
  };
  const midDepth = new Map();
  const depthIn = (v) => {
    if (!midDepth.has(v)) midDepth.set(v, median(solid.filter((i) => views[v].pts.has(i)).map((i) => camZ(v, i))) || median(solid.map((i) => camZ(v, i))) || 1);
    return midDepth.get(v);
  };
  // nearest solid light on each side within the same run (-1 if none)
  const prevS = new Int32Array(n).fill(-1), nextS = new Int32Array(n).fill(-1);
  for (let i = 0, last = -1; i < n; i++) {
    if (i > 0 && runOf[i] !== runOf[i - 1]) last = -1;
    prevS[i] = st.has[i] ? i : last;
    if (st.has[i]) last = i;
  }
  for (let i = n - 1, nxt = -1; i >= 0; i--) {
    if (i < n - 1 && runOf[i] !== runOf[i + 1]) nxt = -1;
    nextS[i] = st.has[i] ? i : nxt;
    if (st.has[i]) nxt = i;
  }
  const maxBridge = opts.maxBridge ?? (useRuns ? Infinity : 60);
  for (let i = 0; i < n; i++) {
    if (st.has[i]) continue;
    est[i] = 1;
    const lo = prevS[i], hi = nextS[i];
    const seenIn = [...st.reg].filter((v) => views[v].pts.has(i));
    if (seenIn.length) {
      const v = seenIn[0];
      const dl = lo >= 0 && (useRuns || views[v].pts.has(lo)) ? camZ(v, lo) : null, dh = hi >= 0 && (useRuns || views[v].pts.has(hi)) ? camZ(v, hi) : null;
      let d;
      if (dl !== null && dh !== null && hi - lo <= 200) d = dl + (dh - dl) * ((i - lo) / (hi - lo));
      else d = dl ?? dh ?? depthIn(v);
      const b = bearing(views[v].pts.get(i), st.F);
      const q = mv(T, mtv(st.R[v], sub([b[0] * d, b[1] * d, d], st.t[v])));
      pts[3 * i] = q[0];
      pts[3 * i + 1] = q[1];
      pts[3 * i + 2] = q[2];
    } else if (lo >= 0 && hi >= 0 && hi - lo <= maxBridge) {
      const f = (i - lo) / (hi - lo);
      for (let c = 0; c < 3; c++) pts[3 * i + c] = pts[3 * lo + c] + (pts[3 * hi + c] - pts[3 * lo + c]) * f;
    } else {
      let near = lo >= 0 && (hi < 0 || i - lo <= hi - i) ? lo : hi; // beyond the ends: the nearest solid light's place
      if (near < 0 && !useRuns) near = -1;
      if (near < 0) {
        // a run with nothing solid in it (or no runs): the closest solid light by index, if any
        let bd = Infinity;
        for (const j of solid) if (Math.abs(j - i) < bd) (bd = Math.abs(j - i)), (near = j);
      }
      for (let c = 0; c < 3; c++) pts[3 * i + c] = near >= 0 ? pts[3 * near + c] : 0;
    }
  }
  // fit into the unit box with the biggest extent spanning 90% (solid points decide the extents)
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const i of solid) for (let c = 0; c < 3; c++) (lo[c] = Math.min(lo[c], pts[3 * i + c])), (hi[c] = Math.max(hi[c], pts[3 * i + c]));
  const ext = Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2], 1e-9), kk = 0.9 / ext, ctr = [0, 1, 2].map((c) => (lo[c] + hi[c]) / 2);
  const pos = new Float32Array(3 * n);
  for (let i = 0; i < n; i++) for (let c = 0; c < 3; c++) pos[3 * i + c] = 0.5 + (pts[3 * i + c] - ctr[c]) * kk;
  const used = new Uint8Array(n), errPx = new Float32Array(n);
  for (const v of st.reg) for (const i of views[v].pts.keys()) if (st.has[i] && !st.bad.has(v * n + i)) used[i]++;
  {
    const sq = new Float64Array(n);
    for (const [key, e] of rep.per) {
      const i = key % n;
      if (st.has[i]) sq[i] += e * e;
    }
    for (let i = 0; i < n; i++) if (used[i]) errPx[i] = Math.sqrt(sq[i] / used[i]);
  }
  // per-view diagnostics
  const placedNames = [...st.reg].map((v) => v + 1).join(", ");
  const outViews = views.map((vw, v) => {
    if (!st.reg.has(v)) {
      const w = best.why.get(v);
      let shared = 0;
      for (const i of vw.pts.keys()) if (st.has[i]) shared++;
      let error;
      if (sharedWith[v] < minShared) error = `${label(v)} shares at most ${sharedWith[v]} lights with any other view (it needs ${minShared}). Scan it again from an angle that sees the same strips, closer to another angle.`;
      else if (w && w.seen < 6) error = `${label(v)} sees only ${w.seen} of the lights placed from views ${placedNames} (it needs 6). Scan again from an angle closer to those, with the same strips in view.`;
      else if (w) error = `${label(v)} could not be joined: only ${w.inl} of the ${w.seen} lights it shares with views ${placedNames} fit one camera position. Its angle is probably too different, or the matches are too noisy; try an angle closer to the others.`;
      else error = `${label(v)} could not be joined to the others.`;
      warnings.push(error);
      return { ok: false, count: vw.pts.size, shared, error };
    }
    const Rout = mm(st.R[v], mt(T)); // output frame -> camera
    const c0 = mtv(st.R[v], [-st.t[v][0], -st.t[v][1], -st.t[v][2]]);
    const cOut = mv(T, c0);
    const fwd = mv(T, mtv(st.R[v], [0, 0, 1]));
    const pv = rep.perView.get(v);
    let inl = 0, shared = 0, tot = 0;
    for (const i of vw.pts.keys()) {
      if (st.has[i]) shared++;
      if (st.has[i] && !st.bad.has(v * n + i)) inl++;
    }
    tot = vw.pts.size;
    const vr = pv && pv.n ? Math.sqrt(pv.sq / pv.n) : 0;
    const out = {
      ok: true,
      rms: vr,
      count: tot,
      used: pv ? pv.n : 0,
      inliers: inl,
      shared,
      center: [0.5 + (cOut[0] - ctr[0]) * kk, 0.5 + (cOut[1] - ctr[1]) * kk, 0.5 + (cOut[2] - ctr[2]) * kk],
      forward: fwd,
      R: Rout,
      priorDeg: views[v].Rcw && views[best.st.ref].Rcw ? angleBetween(mm(mt(views[v].Rcw), views[best.st.ref].Rcw), st.R[v]) : null,
    };
    if (glow && tot > 0 && inl < 0.4 * tot) {
      out.warning = `${label(v)}: only ${inl} of its ${tot} glow spots fit the 3D model; many spots may be mis-detected.`;
      warnings.push(out.warning);
    }
    return out;
  });
  const noisePx = best.noisePx;
  if (glow && noisePx > 0.7 * tolPx) warnings.push(`The glow centres are noisy (about ${noisePx.toFixed(0)} px); the 3D result is approximate. A darker room and a steadier phone help.`);
  return { ok: true, n, pos, est, count: coverage(viewsIn, n).seen, used, err: errPx, views: outViews, rms: rep.rms, noisePx, fovDeg: fToFov(st.F), found: solid.length, pruned, leveled, ref: st.ref, glow, warnings };
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

// internals exposed for tests and debugging only (not part of the API)
export const _internals = { initialPoses, prepView, fovToF, rng, angleBetween };
