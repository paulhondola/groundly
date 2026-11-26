import type { components } from "../../api/schema.d.ts";

/** What the orb shows. `idle` = no host; `done` = a gate just accepted something (2 s). */
export type OrbMode = "idle" | components["schemas"]["ActivityHost"]["orb"] | "done";

interface Point {
  x: number;
  y: number;
  z: number;
  ring?: boolean;
  u?: number;
}

interface Geometry {
  pts: Point[];
  edges: Array<[number, number]>;
}

export interface Dot {
  x: number;
  y: number;
  r: number;
  alpha: number;
}

export interface Line {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  width: number;
  alpha: number;
}

const SPIN: Record<OrbMode, number> = {
  idle: 0,
  done: 0,
  listening: 0.22,
  reading: 0.35,
  mapping: 0.3,
  writing: 0.8,
};

function fibonacciSphere(n: number): Point[] {
  const pts: Point[] = [];
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < n; i++) {
    const y = 1 - (i / (n - 1)) * 2;
    const r = Math.sqrt(1 - y * y);
    const a = golden * i;
    pts.push({ x: Math.cos(a) * r, y, z: Math.sin(a) * r });
  }
  return pts;
}

/** Each state borrows a shape from Groundly: a ring, a globe, lines of text, a topic graph, a loop. */
export function geometry(mode: OrbMode, size: number): Geometry {
  // Below 28 px the dot count drops so the small orb stays crisp.
  const small = size <= 28;
  const pts: Point[] = [];
  const edges: Array<[number, number]> = [];
  if (mode === "idle") {
    const n = small ? 12 : 20;
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      pts.push({ x: Math.cos(a) * 0.82, y: Math.sin(a) * 0.82, z: 0 });
    }
  } else if (mode === "done") {
    const n = small ? 12 : 24;
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      pts.push({ x: Math.cos(a) * 0.86, y: Math.sin(a) * 0.86, z: 0, ring: true });
    }
    const seg = [
      [-0.42, 0.04],
      [-0.12, 0.34],
      [0.46, -0.3],
    ];
    const m = small ? 4 : 7;
    for (let s = 0; s < 2; s++) {
      for (let i = s === 1 ? 1 : 0; i <= m; i++) {
        const f = i / m;
        const [ax, ay] = seg[s];
        const [bx, by] = seg[s + 1];
        pts.push({ x: ax + (bx - ax) * f, y: ay + (by - ay) * f, z: 0 });
      }
    }
  } else if (mode === "listening") {
    return { pts: fibonacciSphere(small ? 36 : 120), edges };
  } else if (mode === "reading") {
    const bands = small ? 6 : 11;
    const perBand = small ? 10 : 26;
    for (let k = 0; k < bands; k++) {
      const lat = -Math.PI / 2 + (Math.PI * (k + 0.5)) / bands;
      const c = Math.max(3, Math.round(perBand * Math.cos(lat)));
      for (let j = 0; j < c; j++) {
        const lo = (j / c) * Math.PI * 2;
        pts.push({
          x: Math.cos(lat) * Math.cos(lo),
          y: Math.sin(lat),
          z: Math.cos(lat) * Math.sin(lo),
        });
      }
    }
  } else if (mode === "mapping") {
    const nodes = fibonacciSphere(small ? 12 : 26);
    const seen = new Set<string>();
    nodes.forEach((p, i) => {
      const near = nodes
        .map((q, j) => ({ j, d: (p.x - q.x) ** 2 + (p.y - q.y) ** 2 + (p.z - q.z) ** 2 }))
        .filter((o) => o.j !== i)
        .sort((a, b) => a.d - b.d)
        .slice(0, 2);
      for (const o of near) {
        const key = Math.min(i, o.j) + "-" + Math.max(i, o.j);
        if (!seen.has(key)) {
          seen.add(key);
          edges.push([i, o.j]);
        }
      }
    });
    return { pts: nodes, edges };
  } else {
    const around = small ? 20 : 44;
    const tube = small ? 4 : 8;
    const big = 0.64;
    const thin = 0.28;
    for (let i = 0; i < around; i++) {
      for (let j = 0; j < tube; j++) {
        const u = (i / around) * Math.PI * 2;
        const v = (j / tube) * Math.PI * 2;
        pts.push({
          x: (big + thin * Math.cos(v)) * Math.cos(u),
          y: thin * Math.sin(v),
          z: (big + thin * Math.cos(v)) * Math.sin(u),
          u,
        });
      }
    }
  }
  return { pts, edges };
}

const clamp01 = (v: number) => Math.max(0, Math.min(1, v));

/** One frame at time `t` seconds, in CSS pixels of a `size` square. */
export function frame(geo: Geometry, mode: OrbMode, size: number, t: number) {
  const half = size / 2;
  const rad = size * 0.44;
  const base = Math.max(1, size / 70);
  const flat = mode === "idle" || mode === "done";
  const tilt = mode === "writing" ? 1.05 + Math.sin(t * 0.7) * 0.15 : 0.42;
  const ay = t * SPIN[mode];
  const [cy, sy, cx, sx] = [Math.cos(ay), Math.sin(ay), Math.cos(tilt), Math.sin(tilt)];
  const proj = geo.pts.map((p) => {
    if (flat) {
      return { x: p.x, y: p.y, z: 0 };
    }
    const x1 = p.x * cy + p.z * sy;
    const z1 = -p.x * sy + p.z * cy;
    return { x: x1, y: p.y * cx - z1 * sx, z: p.y * sx + z1 * cx };
  });
  const scan = Math.sin(t * 1.3) * 0.95;
  const hot = geo.pts.length ? Math.floor(t * 1.6) % geo.pts.length : -1;

  const dots: Dot[] = proj.map((q, i) => {
    const depth = (q.z + 1) / 2;
    let alpha: number;
    let s = base * (0.7 + 0.9 * depth);
    if (mode === "idle") {
      alpha = 0.42;
      s = base * 1.2;
    } else if (mode === "done") {
      const ring = geo.pts[i].ring === true;
      alpha = ring ? 0.22 : 1;
      s = base * (ring ? 1 : 1.7);
    } else if (mode === "listening") {
      alpha = 0.12 + 0.7 * depth;
    } else if (mode === "reading") {
      const glow = Math.exp(-((q.y - scan) ** 2) / 0.025);
      alpha = 0.1 + 0.35 * depth + 0.75 * glow * (0.4 + 0.6 * depth);
      s *= 1 + 0.5 * glow;
    } else if (mode === "mapping") {
      alpha = i === hot ? 1 : 0.3 + 0.7 * depth;
      s *= i === hot ? 2.2 : 1.4;
    } else {
      const wave = 0.5 + 0.5 * Math.sin((geo.pts[i].u ?? 0) * 2 - t * 4);
      alpha = (0.1 + 0.6 * depth) * (0.35 + 0.65 * wave);
    }
    return { x: half + q.x * rad, y: half + q.y * rad, r: s / 2, alpha: clamp01(alpha) };
  });

  const lines: Line[] = geo.edges.map(([a, b]) => {
    const pa = proj[a];
    const pb = proj[b];
    const depth = Math.min((pa.z + 1) / 2, (pb.z + 1) / 2);
    const lit = a === hot || b === hot;
    return {
      x1: half + pa.x * rad,
      y1: half + pa.y * rad,
      x2: half + pb.x * rad,
      y2: half + pb.y * rad,
      width: size > 40 ? 1 : 0.75,
      alpha: lit ? 0.75 : 0.06 + 0.3 * depth,
    };
  });
  return { dots, lines };
}

/** The orb for the activity feed: no host is idle, any working host wins, else listening. */
export function orbModeForHosts(
  hosts: ReadonlyArray<{ orb: Exclude<OrbMode, "idle" | "done"> }>,
): OrbMode {
  if (hosts.length === 0) {
    return "idle";
  }
  for (const mode of ["writing", "mapping", "reading"] as const) {
    if (hosts.some((h) => h.orb === mode)) {
      return mode;
    }
  }
  return "listening";
}
