import { describe, expect, test } from "vitest";

import type { AnalyticsMaturity, AnalyticsPoint, SurfaceSlice } from "../api";
import {
  callDeltaConvention,
  cleanDenseSurface,
  cleanSmile,
  cleanSurfaceMaturities,
  flaggedNote,
  isCleanSlice,
  isFiniteNumber,
  isSaneIv,
  IV_SANE_MAX,
  singleBranchDeltaPoints,
} from "./volRobust";

// --- independently-derived fixtures for the clean-surface / single-branch helpers ----------------
// Minimal hand-built objects so the expected values below are derived by reading these literals, not
// by reusing a shared fixture. Only the fields the helpers read are populated; the rest are stubbed.

function slice(overrides: { degenerate: boolean; arb_free: boolean }): SurfaceSlice {
  return {
    snapshot_ts: "",
    underlying: "TEST",
    maturity_years: 0.25,
    model_version: "test",
    svi_a: 0,
    svi_b: 0,
    svi_rho: 0,
    svi_m: 0,
    svi_sigma: 0,
    expiry_date: "",
    day_count: "ACT/365",
    diagnostics: {
      rmse: 0,
      n_points: 10,
      arb_free: overrides.arb_free,
      bound_hits: null,
      converged: true,
    },
    degenerate: overrides.degenerate,
    degenerate_reasons: [],
    source_snapshot_ts: "",
    provenance: {
      calc_ts: "",
      code_version: "",
      config_hashes: {},
      stamp_hash: "",
      n_sources: 0,
    },
  };
}

function maturity(
  label: string,
  surface_slice: SurfaceSlice | null,
  points: AnalyticsPoint[] = [],
): AnalyticsMaturity {
  return {
    maturity_years: 0.25,
    tenor_label: label,
    label,
    smile: { axis_type: "delta", deltas: [], implied_vols: [], log_moneyness: [] },
    surface_slice,
    points,
  };
}

function point(strike: number, rawDelta: number): AnalyticsPoint {
  const m = (raw: number) => ({ raw, dollar: 0, unit: "" });
  return {
    delta_band: rawDelta < 0 ? "p" : "c",
    target_delta: 0,
    log_moneyness: 0,
    strike,
    forward_price: 0,
    implied_vol: 0.2,
    total_variance: 0,
    price: 0,
    metrics: {
      delta: m(rawDelta),
      gamma: m(0),
      vega: m(0),
      theta: m(0),
      rho: m(0),
    },
    provenance: {
      calc_ts: "",
      code_version: "",
      config_hashes: {},
      stamp_hash: "",
      n_sources: 0,
    },
  };
}

describe("isSaneIv", () => {
  test("accepts a finite IV inside the sane band", () => {
    expect(isSaneIv(0.16)).toBe(true);
    expect(isSaneIv(IV_SANE_MAX)).toBe(true);
  });
  test("rejects non-finite, non-positive, and absurd railed IVs", () => {
    expect(isSaneIv(Number.NaN)).toBe(false);
    expect(isSaneIv(Number.POSITIVE_INFINITY)).toBe(false);
    expect(isSaneIv(0)).toBe(false);
    expect(isSaneIv(-0.1)).toBe(false);
    expect(isSaneIv(1.08)).toBe(false);
    expect(isSaneIv(1.4)).toBe(false);
    expect(isSaneIv(null)).toBe(false);
    expect(isSaneIv(undefined)).toBe(false);
  });
});

describe("isFiniteNumber", () => {
  test("excludes null/undefined/NaN/Infinity", () => {
    expect(isFiniteNumber(0)).toBe(true);
    expect(isFiniteNumber(-1.5)).toBe(true);
    expect(isFiniteNumber(null)).toBe(false);
    expect(isFiniteNumber(undefined)).toBe(false);
    expect(isFiniteNumber(Number.NaN)).toBe(false);
    expect(isFiniteNumber(Number.POSITIVE_INFINITY)).toBe(false);
  });
});

describe("cleanSmile (degenerate slice)", () => {
  const ks = [-0.03, -0.18, -0.25, 0.0, 0.0, 0.03];
  const ivs = [0.19, 1.08, Number.NaN, 0.152, 0.152, 0.143];

  test("drops absurd, non-finite, and duplicate-k points; keeps the good ones", () => {
    const r = cleanSmile(ks, ivs);

    expect(r.logMoneyness).toEqual([-0.03, 0.0, 0.03]);
    expect(r.impliedVols).toEqual([0.19, 0.152, 0.143]);
    expect(r.nDroppedAbsurd).toBe(1);
    expect(r.nDroppedNonFinite).toBe(1);
    expect(r.nDroppedDuplicate).toBe(1);
  });

  test("a clean slice drops nothing", () => {
    const r = cleanSmile([-0.05, 0.0, 0.05], [0.2, 0.16, 0.15]);
    expect(r.impliedVols).toEqual([0.2, 0.16, 0.15]);
    expect(r.nDroppedAbsurd + r.nDroppedNonFinite + r.nDroppedDuplicate).toBe(0);
  });

  test("never mutates the caller's arrays", () => {
    const inputKs = [-0.03, 0.0, 0.0];
    const inputIvs = [0.19, 1.08, 0.152];
    const before = JSON.stringify({ inputKs, inputIvs });
    cleanSmile(inputKs, inputIvs);
    expect(JSON.stringify({ inputKs, inputIvs })).toBe(before);
  });

  // infra-surface-fit-quality LANE 2 (front robustness): the ~2-3d ultra-short slice serves wing IV
  // spikes to 1.0–1.4 (10d-wing extrapolation, audit F3). The clean must exclude every such spike
  // so the smile/surface can't draw a garbage peak — flag-not-reject, render-side only.
  test("excludes the ultra-short wing IV spikes (1.0–1.4) the 10d slice serves", () => {
    const wingKs = [-0.25, -0.18, 0.0, 0.18, 0.25];
    const wingIvs = [1.4, 1.08, 0.152, 1.0, 1.2];
    const r = cleanSmile(wingKs, wingIvs);
    // Only the in-band ATM neighbourhood survives; all four wing spikes are dropped as absurd.
    expect(r.logMoneyness).toEqual([0.0]);
    expect(r.impliedVols).toEqual([0.152]);
    expect(r.nDroppedAbsurd).toBe(4);
    for (const iv of r.impliedVols) expect(iv).toBeLessThanOrEqual(IV_SANE_MAX);
  });
});

describe("cleanDenseSurface (railed slice cannot blow the surface)", () => {
  const logMoneyness = [-0.2, -0.1, -0.1, 0.0, 0.1];
  const maturityYears = [0.03, 1.0];
  const impliedVol = [
    [1.4, 0.55, 0.55, 0.15, 0.11],
    [0.24, 0.22, 0.22, 0.21, 0.2],
  ];

  test("clamps out-of-band cells to null holes, collapses duplicate columns, counts flags", () => {
    const r = cleanDenseSurface(logMoneyness, maturityYears, impliedVol);

    expect(r.logMoneyness).toEqual([-0.2, -0.1, 0.0, 0.1]);

    expect(r.impliedVol).toEqual([
      [null, 0.55, 0.15, 0.11],
      [0.24, 0.22, 0.21, 0.2],
    ]);
    expect(r.nFlaggedCells).toBe(1);
    expect(r.nFlaggedSlices).toBe(1);

    const survivors = r.impliedVol.flat().filter((v): v is number => v !== null);
    expect(Math.max(...survivors)).toBeLessThanOrEqual(IV_SANE_MAX);
  });

  test("does not mutate the served surface arrays", () => {
    const iv = [
      [1.4, 0.15],
      [0.24, 0.21],
    ];
    const before = JSON.stringify(iv);
    cleanDenseSurface([-0.2, 0.0], [0.03, 1.0], iv);
    expect(JSON.stringify(iv)).toBe(before);
  });
});

describe("flaggedNote", () => {
  test("null when nothing flagged; pluralises otherwise", () => {
    expect(flaggedNote(0, "slice")).toBeNull();
    expect(flaggedNote(1, "slice")).toBe("1 slice flagged (excluded from view)");
    expect(flaggedNote(3, "slice")).toBe("3 slices flagged (excluded from view)");
  });
});

describe("isCleanSlice", () => {
  test("a clean fit (not degenerate, arb-free) is clean", () => {
    expect(isCleanSlice(slice({ degenerate: false, arb_free: true }))).toBe(true);
  });
  test("a degenerate slice is not clean", () => {
    expect(isCleanSlice(slice({ degenerate: true, arb_free: true }))).toBe(false);
  });
  test("a non-arb-free slice is not clean", () => {
    expect(isCleanSlice(slice({ degenerate: false, arb_free: false }))).toBe(false);
  });
  test("a missing slice (surface-grid fallback) is treated as clean, not flagged", () => {
    expect(isCleanSlice(null)).toBe(true);
    expect(isCleanSlice(undefined)).toBe(true);
  });
});

describe("cleanSurfaceMaturities", () => {
  // Four maturities: one clean fit, one degenerate, one non-arb-free, one with no per-slice fit.
  const clean = maturity("3m", slice({ degenerate: false, arb_free: true }));
  const degen = maturity("1w", slice({ degenerate: true, arb_free: true }));
  const arbViol = maturity("2w", slice({ degenerate: false, arb_free: false }));
  const noSlice = maturity("6m", null);
  const all = [clean, degen, arbViol, noSlice];

  test("drops the degenerate and non-arb-free slices, keeps clean + no-slice", () => {
    const kept = cleanSurfaceMaturities(all);
    expect(kept.map((m) => m.label)).toEqual(["3m", "6m"]);
  });

  test("a fully clean set passes through unchanged", () => {
    const kept = cleanSurfaceMaturities([clean, noSlice]);
    expect(kept).toHaveLength(2);
  });

  test("does not mutate the caller's array", () => {
    const before = all.map((m) => m.label);
    cleanSurfaceMaturities(all);
    expect(all.map((m) => m.label)).toEqual(before);
  });
});

describe("callDeltaConvention", () => {
  test("lifts put-quoted (negative) deltas onto the call branch by +1", () => {
    // A 50-delta put (-0.50) is a 50-delta call (+0.50) under parity.
    expect(callDeltaConvention(-0.5)).toBeCloseTo(0.5, 12);
    // A 2-delta put (-0.02) is a deep in-the-money call (0.98).
    expect(callDeltaConvention(-0.02)).toBeCloseTo(0.98, 12);
  });
  test("leaves call-quoted (non-negative) deltas unchanged", () => {
    expect(callDeltaConvention(0.3)).toBe(0.3);
    expect(callDeltaConvention(0)).toBe(0);
  });
});

describe("singleBranchDeltaPoints (the at-the-money delta spike fix)", () => {
  // A miniature smile reproducing the bug: put-quoted low-strike wing (negative delta), a duplicated
  // at-the-money strike (atmp -0.5 and atm +0.5), then a call-quoted high-strike wing (positive
  // delta). Raw, plotting delta vs strike spikes from -0.5 to +0.5 at strike 6300.
  const raw = [
    point(6000, -0.3), // put wing
    point(6300, -0.5), // atmp (duplicate strike)
    point(6300, 0.5), // atm  (duplicate strike)
    point(6600, 0.3), // call wing
  ];

  test("collapses the duplicate strike and yields a monotone, non-spiking call-delta curve", () => {
    const out = singleBranchDeltaPoints(raw);

    // Duplicate strike 6300 collapses to one point.
    expect(out.map((p) => p.strike)).toEqual([6000, 6300, 6600]);

    // Every delta is now on the call convention: put-wing -0.3 -> 0.7, atm -0.5 -> 0.5, call 0.3.
    const deltas = out.map((p) => p.metrics.delta.raw);
    expect(deltas[0]).toBeCloseTo(0.7, 12); // 6000: -0.3 + 1
    expect(deltas[1]).toBeCloseTo(0.5, 12); // 6300: -0.5 + 1 (atmp kept first)
    expect(deltas[2]).toBeCloseTo(0.3, 12); // 6600: already a call delta

    // Monotone decreasing => no vertical spike (the bug was a sign flip mid-curve).
    for (let i = 1; i < deltas.length; i += 1) {
      expect(deltas[i]).toBeLessThan(deltas[i - 1]);
    }
    // The impossible jump (|Δdelta| ~ 1.0 within one strike step) is gone: every step is small.
    for (let i = 1; i < deltas.length; i += 1) {
      expect(Math.abs(deltas[i] - deltas[i - 1])).toBeLessThan(0.5);
    }
  });

  test("does not mutate the input points or their metrics", () => {
    const before = JSON.stringify(raw);
    singleBranchDeltaPoints(raw);
    expect(JSON.stringify(raw)).toBe(before);
  });

  test("an already-single-branch curve is returned sorted, unduplicated", () => {
    const callsOnly = [point(6600, 0.3), point(6000, 0.7), point(6300, 0.5)];
    const out = singleBranchDeltaPoints(callsOnly);
    expect(out.map((p) => p.strike)).toEqual([6000, 6300, 6600]);
    expect(out.map((p) => p.metrics.delta.raw)).toEqual([0.7, 0.5, 0.3]);
  });
});
