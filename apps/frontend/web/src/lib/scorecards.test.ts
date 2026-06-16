import { describe, expect, test } from "vitest";

import type { AnalyticsMaturity, AnalyticsPoint } from "../api";
import { atmIv, computeScorecards, ivAtDelta, referenceMaturity } from "./scorecards";

const PROV = {
  calc_ts: "2026-05-29T15:31:00+00:00",
  code_version: "abc",
  config_hash: "cfg",
  stamp_hash: "stamp",
  n_sources: 1,
};

// A point carrying only the fields the scorecard math reads (delta band, target delta, IV). The
// rest is filled with inert values so the type is satisfied.
function point(targetDelta: number, iv: number): AnalyticsPoint {
  return {
    delta_band: `${targetDelta}`,
    target_delta: targetDelta,
    log_moneyness: 0,
    strike: 100,
    forward_price: 100,
    implied_vol: iv,
    total_variance: 0,
    price: 0,
    metrics: {
      delta: { raw: 0, dollar: 0, unit: null },
      gamma: { raw: 0, dollar: 0, unit: null },
      vega: { raw: 0, dollar: 0, unit: null },
      theta: { raw: 0, dollar: 0, unit: null },
      rho: { raw: 0, dollar: 0, unit: null },
    },
    provenance: PROV,
  };
}

function maturity(
  years: number,
  tenor: string,
  smileK: number[],
  smileIv: number[],
  points: AnalyticsPoint[],
): AnalyticsMaturity {
  return {
    maturity_years: years,
    tenor_label: tenor,
    label: `${tenor} (${years.toFixed(3)}y)`,
    smile: { axis_type: "delta", deltas: [], implied_vols: smileIv, log_moneyness: smileK },
    surface_slice: null,
    points,
  };
}

describe("referenceMaturity", () => {
  test("picks the slice closest to 3m (0.25y)", () => {
    const m1 = maturity(0.083, "1m", [], [], []);
    const m3 = maturity(0.25, "3m", [], [], []);
    const m12 = maturity(1.0, "12m", [], [], []);
    expect(referenceMaturity([m1, m3, m12])?.tenor_label).toBe("3m");
  });

  test("falls to the nearest tenor when 3m is absent", () => {
    const m1 = maturity(0.083, "1m", [], [], []);
    const m6 = maturity(0.5, "6m", [], [], []);
    // |0.083-0.25|=0.167 vs |0.5-0.25|=0.25 → 1m is nearer.
    expect(referenceMaturity([m1, m6])?.tenor_label).toBe("1m");
  });

  test("returns null for an empty term structure", () => {
    expect(referenceMaturity([])).toBeNull();
  });
});

describe("atmIv", () => {
  test("reads the sane smile point nearest log-moneyness 0", () => {
    // k = +0.05 is nearer 0 than k = −0.10, so its IV (0.20) is ATM.
    const m = maturity(0.25, "3m", [-0.1, 0.05, 0.2], [0.24, 0.2, 0.22], []);
    expect(atmIv(m)).toBe(0.2);
  });

  test("skips a railed (out-of-sane-band) ATM cell", () => {
    // The point nearest 0 (k=0.0) is 1.5 (railed); the next nearest sane point (k=0.1, IV 0.21) wins.
    const m = maturity(0.25, "3m", [0.0, 0.1], [1.5, 0.21], []);
    expect(atmIv(m)).toBe(0.21);
  });
});

describe("ivAtDelta", () => {
  test("returns an exact captured band verbatim", () => {
    const m = maturity(0.25, "3m", [], [], [point(-0.25, 0.3), point(0.25, 0.22)]);
    expect(ivAtDelta(m, -0.25)).toBe(0.3);
    expect(ivAtDelta(m, 0.25)).toBe(0.22);
  });

  test("linearly interpolates between the bracketing bands", () => {
    // Target −0.25 sits halfway between −0.30 (IV 0.32) and −0.20 (IV 0.28) → 0.30.
    const m = maturity(0.25, "3m", [], [], [point(-0.3, 0.32), point(-0.2, 0.28)]);
    expect(ivAtDelta(m, -0.25)).toBeCloseTo(0.3, 10);
  });

  test("returns null when the grid does not reach the wing (no bracket)", () => {
    // Only a −0.30 band exists; there is no band ≥ −0.25, so the call-ward side of the bracket is
    // missing and we do not extrapolate.
    const m = maturity(0.25, "3m", [], [], [point(-0.3, 0.32)]);
    expect(ivAtDelta(m, -0.25)).toBeNull();
  });
});

describe("computeScorecards", () => {
  test("computes level, 25Δ skew and butterfly at the reference tenor", () => {
    // ATM smile: nearest 0 is k=0.0 → IV 0.20. Bands bracket ±0.25: put −0.30→0.30, −0.20→0.26
    // (−0.25 interp = 0.28); call +0.20→0.22, +0.30→0.24 (+0.25 interp = 0.23).
    // skew = 0.28 − 0.23 = 0.05; butterfly = 0.28 + 0.23 − 2·0.20 = 0.11.
    const m = maturity(
      0.25,
      "3m",
      [-0.1, 0.0, 0.1],
      [0.24, 0.2, 0.22],
      [point(-0.3, 0.3), point(-0.2, 0.26), point(0.2, 0.22), point(0.3, 0.24)],
    );
    const card = computeScorecards([m]);
    expect(card).not.toBeNull();
    expect(card?.atm).toBeCloseTo(0.2, 10);
    expect(card?.skew).toBeCloseTo(0.05, 10);
    expect(card?.convexity).toBeCloseTo(0.11, 10);
    expect(card?.isReferenceTenor).toBe(true);
  });

  test("a half-captured smile resolves ATM but leaves the 25Δ metrics null", () => {
    // Only one put band exists → no 25Δ bracket on either side; skew/convexity stay null while ATM
    // still resolves off the smile.
    const m = maturity(0.25, "3m", [-0.1, 0.05], [0.24, 0.21], [point(-0.3, 0.3)]);
    const card = computeScorecards([m]);
    expect(card?.atm).toBe(0.21);
    expect(card?.skew).toBeNull();
    expect(card?.convexity).toBeNull();
  });

  test("flags when the reference tenor fell back to a non-3m slice", () => {
    const m = maturity(1.0, "12m", [0.0], [0.2], []);
    const card = computeScorecards([m]);
    expect(card?.isReferenceTenor).toBe(false);
    expect(card?.tenorLabel).toBe("12m");
  });

  test("returns null for an empty term structure", () => {
    expect(computeScorecards([])).toBeNull();
  });
});
