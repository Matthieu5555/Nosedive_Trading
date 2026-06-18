import type { AnalyticsMaturity, AnalyticsPoint, SurfaceSlice } from "../api";

export const IV_SANE_MAX = 1.0;
export const IV_SANE_MIN = 0;

export function isSaneIv(value: number | null | undefined): value is number {
  return (
    value !== null &&
    value !== undefined &&
    Number.isFinite(value) &&
    value > IV_SANE_MIN &&
    value <= IV_SANE_MAX
  );
}

export function isFiniteNumber(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value);
}

export interface CleanSmileResult {
  logMoneyness: number[];
  impliedVols: number[];

  nDroppedNonFinite: number;
  nDroppedAbsurd: number;
  nDroppedDuplicate: number;
}

export function cleanSmile(
  logMoneyness: readonly number[],
  impliedVols: readonly number[],
): CleanSmileResult {
  const ks: number[] = [];
  const ivs: number[] = [];
  const seenK = new Set<number>();
  let nDroppedNonFinite = 0;
  let nDroppedAbsurd = 0;
  let nDroppedDuplicate = 0;
  const n = Math.min(logMoneyness.length, impliedVols.length);
  for (let i = 0; i < n; i += 1) {
    const k = logMoneyness[i];
    const iv = impliedVols[i];
    if (
      !isFiniteNumber(k) ||
      iv === null ||
      iv === undefined ||
      Number.isNaN(iv) ||
      !Number.isFinite(iv)
    ) {
      nDroppedNonFinite += 1;
      continue;
    }
    if (!isSaneIv(iv)) {
      nDroppedAbsurd += 1;
      continue;
    }
    if (seenK.has(k)) {
      nDroppedDuplicate += 1;
      continue;
    }
    seenK.add(k);
    ks.push(k);
    ivs.push(iv);
  }
  return {
    logMoneyness: ks,
    impliedVols: ivs,
    nDroppedNonFinite,
    nDroppedAbsurd,
    nDroppedDuplicate,
  };
}

export interface CleanDenseSurfaceResult {
  logMoneyness: number[];
  maturityYears: number[];

  impliedVol: (number | null)[][];

  nFlaggedCells: number;
  nFlaggedSlices: number;
}

export function cleanDenseSurface(
  logMoneyness: readonly number[],
  maturityYears: readonly number[],
  impliedVol: ReadonlyArray<ReadonlyArray<number | null>>,
): CleanDenseSurfaceResult {
  const seenK = new Set<number>();
  const keptCols: number[] = [];
  const keptK: number[] = [];
  logMoneyness.forEach((k, j) => {
    if (isFiniteNumber(k) && !seenK.has(k)) {
      seenK.add(k);
      keptCols.push(j);
      keptK.push(k);
    }
  });
  let nFlaggedCells = 0;
  let nFlaggedSlices = 0;
  const cleaned: (number | null)[][] = maturityYears.map((_, i) => {
    const row = impliedVol[i] ?? [];
    let sliceFlagged = false;
    const cleanedRow = keptCols.map((j) => {
      const v = row[j];
      if (isSaneIv(v)) return v;
      nFlaggedCells += 1;
      sliceFlagged = true;
      return null;
    });
    if (sliceFlagged) nFlaggedSlices += 1;
    return cleanedRow;
  });
  return {
    logMoneyness: keptK,
    maturityYears: [...maturityYears],
    impliedVol: cleaned,
    nFlaggedCells,
    nFlaggedSlices,
  };
}

export function flaggedNote(count: number, noun: string): string | null {
  if (count <= 0) return null;
  return `${count} ${noun}${count === 1 ? "" : "s"} flagged (excluded from view)`;
}

// --- clean-surface slice filter (front-week / degenerate slices) ---------------------------------
// The SVI fitter tags a slice it could not fit cleanly: `degenerate === true` (front-week or too few
// points to pin the wings) or `diagnostics.arb_free === false` (the fitted smile admits a calendar /
// butterfly arbitrage). Those slices produce nonsense Greek curves — most visibly an impossible
// vertical delta spike where the put and call branches cross at the at-the-money strike — so the
// default "clean surface" view drops them. The flags live on `AnalyticsMaturity.surface_slice`
// (see api.ts SurfaceSlice). A maturity with no `surface_slice` (the surface-grid fallback path,
// where no per-slice fit was banked) is NOT assumed degenerate: absence of a flag is not a failure
// flag, so it is kept.

export function isCleanSlice(slice: SurfaceSlice | null | undefined): boolean {
  if (slice === null || slice === undefined) return true;
  if (slice.degenerate === true) return false;
  if (slice.diagnostics?.arb_free === false) return false;
  return true;
}

// Keep only the maturities whose fitted slice is clean (or has no per-slice fit). Pure: returns a
// new array, never mutates. This is the default view; passing the raw array (the "show all slices"
// toggle) bypasses it.
export function cleanSurfaceMaturities(
  maturities: readonly AnalyticsMaturity[],
): AnalyticsMaturity[] {
  return maturities.filter((m) => isCleanSlice(m.surface_slice));
}

// --- single-branch delta curve (the at-the-money spike fix) --------------------------------------
// A single smile carries BOTH branches: put-quoted points on the low-strike wing (delta in [-1, 0])
// and call-quoted points on the high-strike wing (delta in [0, 1]), with the at-the-money strike
// appearing twice (e.g. atmp delta -0.50 and atm delta +0.50 at the same strike). Plotting raw delta
// against strike connects the negative put branch to the positive call branch through that duplicate
// strike, drawing an impossible vertical spike from about -0.5 to +0.5 within one strike step.
//
// The fix puts every point on ONE convention: the call delta. For a put-quoted point, call delta =
// put delta + 1 (put-call parity on delta). That turns the two branches into a single continuous,
// monotone-decreasing S-curve (deep in-the-money call delta near 1 at low strikes, near 0 far out of
// the money at high strikes), and the duplicate at-the-money strike collapses to a single ~0.5 point.
// Returns points sorted by strike, deduplicated by strike, with `metrics.delta.raw` rewritten to the
// call-delta convention. Pure: builds new point objects, never mutates the input.

export function callDeltaConvention(rawDelta: number): number {
  // Put-quoted points carry a negative delta; lift them onto the call branch (+1). Call-quoted
  // points (already non-negative) are unchanged.
  return rawDelta < 0 ? rawDelta + 1 : rawDelta;
}

export function singleBranchDeltaPoints(points: readonly AnalyticsPoint[]): AnalyticsPoint[] {
  const byStrike = new Map<number, AnalyticsPoint>();
  for (const p of points) {
    const callDelta = isFiniteNumber(p.metrics?.delta?.raw)
      ? callDeltaConvention(p.metrics.delta.raw)
      : p.metrics?.delta?.raw;
    const normalized: AnalyticsPoint = {
      ...p,
      metrics: { ...p.metrics, delta: { ...p.metrics.delta, raw: callDelta } },
    };
    // The at-the-money strike appears twice (put-quoted + call-quoted). Both map to ~0.5 under the
    // call convention, so either is fine; keep the first seen for determinism.
    if (!byStrike.has(p.strike)) byStrike.set(p.strike, normalized);
  }
  return [...byStrike.values()].sort((a, b) => a.strike - b.strike);
}
