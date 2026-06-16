import { isSaneIv } from "./volRobust";

// Pure structural shapes (a subset of api.ts's AnalyticsMaturity / AnalyticsPoint), declared here so
// this stays a leaf `lib` module that never imports the `api` transport layer (the boundaries DAG:
// lib → api, never the reverse). The components pass the full api types, which satisfy these
// structurally.
export interface ScorecardSmile {
  log_moneyness: number[];
  implied_vols: number[];
}

export interface ScorecardPoint {
  target_delta: number;
  implied_vol: number;
}

export interface ScorecardMaturity {
  maturity_years: number;
  tenor_label: string;
  label: string;
  smile: ScorecardSmile;
  points: ScorecardPoint[];
}

// The reference tenor the scorecards read at — the blueprint's signal tenor (configs/universe.yaml
// signals.reference_tenor). When the captured grid has no 3m slice we fall to the nearest tenor and
// say so, never fabricate one.
export const REFERENCE_TENOR_YEARS = 0.25;

// The wing the 25Δ risk-reversal / butterfly are read at. The platform projects a ±0.30 delta band
// in 0.02 steps, so 0.25 sits inside the grid and is reached by interpolation between the bracketing
// bands; a wing the grid doesn't reach yields no value (shown as "—", never extrapolated).
export const RR_DELTA = 0.25;

export interface Scorecard {
  // The four headline reads; null where the reference slice doesn't carry enough of the smile to
  // compute the metric honestly (the card then shows "—").
  atm: number | null; // ATM implied vol (level)
  skew: number | null; // 25Δ risk-reversal = IV(put 25Δ) − IV(call 25Δ) (slope)
  convexity: number | null; // 25Δ butterfly = IV(25Δp) + IV(25Δc) − 2·ATM (curvature)
  // The tenor actually read (may differ from the requested 3m when 3m wasn't captured).
  tenorLabel: string;
  isReferenceTenor: boolean;
}

// The maturity closest to the reference tenor (3m), by absolute distance in years. Returns null
// only for an empty term structure.
export function referenceMaturity(maturities: ScorecardMaturity[]): ScorecardMaturity | null {
  let best: ScorecardMaturity | null = null;
  let bestDist = Infinity;
  for (const m of maturities) {
    const dist = Math.abs(m.maturity_years - REFERENCE_TENOR_YEARS);
    if (dist < bestDist) {
      bestDist = dist;
      best = m;
    }
  }
  return best;
}

// IV at log-moneyness 0 (at-the-money), read off the cleaned smile: the sane point nearest k = 0.
// We read the smile (not a delta band) because the smile axis is dense and always carries the ATM
// neighbourhood; null when the smile cleans to nothing.
export function atmIv(maturity: ScorecardMaturity): number | null {
  const ks = maturity.smile.log_moneyness;
  const ivs = maturity.smile.implied_vols;
  let best: number | null = null;
  let bestAbs = Infinity;
  const n = Math.min(ks.length, ivs.length);
  for (let i = 0; i < n; i += 1) {
    const k = ks[i];
    const iv = ivs[i];
    if (!Number.isFinite(k) || !isSaneIv(iv)) continue;
    if (Math.abs(k) < bestAbs) {
      bestAbs = Math.abs(k);
      best = iv;
    }
  }
  return best;
}

// IV at a signed target delta, interpolated from the projected delta-band grid (points carry
// target_delta + implied_vol). The grid is ±0.30 in 0.02 steps, so `delta` (±0.25) is bracketed by
// two captured bands and we linearly interpolate between them. If the grid doesn't reach the wing
// (the bracketing band is missing), we return null rather than extrapolate — the blueprint's
// "show the gaps". A railed band (IV out of the sane range) is excluded, so a garbage slice can't
// seed a wing.
export function ivAtDelta(maturity: ScorecardMaturity, delta: number): number | null {
  const sane = maturity.points.filter((p) => isSaneIv(p.implied_vol));
  if (sane.length === 0) return null;
  // An exact captured band wins outright.
  for (const p of sane) {
    if (Math.abs(p.target_delta - delta) < 1e-9) return p.implied_vol;
  }
  // Otherwise bracket: the nearest captured deltas below and above the target. Both must exist —
  // a one-sided neighbour means the grid stops before this wing, so no honest value.
  let lo: { d: number; iv: number } | null = null;
  let hi: { d: number; iv: number } | null = null;
  for (const p of sane) {
    if (p.target_delta <= delta && (lo === null || p.target_delta > lo.d)) {
      lo = { d: p.target_delta, iv: p.implied_vol };
    }
    if (p.target_delta >= delta && (hi === null || p.target_delta < hi.d)) {
      hi = { d: p.target_delta, iv: p.implied_vol };
    }
  }
  if (lo === null || hi === null) return null;
  if (lo.d === hi.d) return lo.iv;
  const t = (delta - lo.d) / (hi.d - lo.d);
  return lo.iv + t * (hi.iv - lo.iv);
}

// The four scorecard reads for a term structure, computed at the reference tenor (3m, else nearest).
// Each metric is independent: ATM may resolve while the 25Δ wings don't (a half-captured smile),
// and the card for a missing metric shows "—". Returns null only when there is no slice at all.
export function computeScorecards(maturities: ScorecardMaturity[]): Scorecard | null {
  const slice = referenceMaturity(maturities);
  if (slice === null) return null;
  const atm = atmIv(slice);
  // 25Δ risk-reversal = put-wing IV − call-wing IV. The platform signs puts negative, calls
  // positive, so the put 25Δ is target_delta −0.25 and the call 25Δ is +0.25.
  const ivPut = ivAtDelta(slice, -RR_DELTA);
  const ivCall = ivAtDelta(slice, RR_DELTA);
  const skew = ivPut !== null && ivCall !== null ? ivPut - ivCall : null;
  // 25Δ butterfly = (25Δp + 25Δc)/… the blueprint's convexity read: IV(25Δp) + IV(25Δc) − 2·ATM.
  const convexity =
    ivPut !== null && ivCall !== null && atm !== null ? ivPut + ivCall - 2 * atm : null;
  return {
    atm,
    skew,
    convexity,
    tenorLabel: slice.tenor_label || slice.label,
    isReferenceTenor: Math.abs(slice.maturity_years - REFERENCE_TENOR_YEARS) < 1e-6,
  };
}
