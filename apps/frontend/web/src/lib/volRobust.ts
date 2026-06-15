// Render-layer robustness for the implied-vol surface and smile.
//
// The backend deliberately FLAGS rather than rejects a degenerate SVI slice (a railed,
// non-converged, or arb-breached calibration is served with `surface_slice.degenerate = true`
// and `surface_fit_error = fail` in QC). That is the blueprint-correct ingestion policy — the
// store must keep the honest, flagged datum. But a railed slice serves absurd implied vols
// (e.g. 108%, 140% at deep-OTM deltas) and exact duplicate points (a duplicated 0.0 delta), and
// a single such slice, plotted raw, blows the whole 3D nappe's height/colour scale and spikes
// every downstream term-structure panel.
//
// These helpers clean ONLY at the render layer: they never mutate the served payload values,
// they exclude/clamp non-finite, out-of-band, and duplicate points for plotting, and they report
// how many points/slices were flagged so the panel can surface an honest "N flagged" note rather
// than rendering garbage. No analytics, fit, or QC logic lives here.

// A sane implied-vol band for index options. The index-option body sits comfortably under ~30%
// IV across the term structure (see the 2026-06-15 SX5E capture: every non-degenerate slice
// peaks near 0.28); a finite IV above this cap is a railed-slice artifact, not a real quote, so
// it is excluded from the surface geometry and the smile, and the colour band is pinned here so
// one railed slice cannot re-stretch the whole scale. This is a display clamp, not a data edit.
export const IV_SANE_MAX = 0.6;
export const IV_SANE_MIN = 0;

// A single implied vol is plottable when it is a finite number inside the sane band. A non-finite
// value (NaN/±∞ from a failed inversion) or an absurd railed value is excluded from the geometry.
export function isSaneIv(value: number | null | undefined): value is number {
  return (
    value !== null &&
    value !== undefined &&
    Number.isFinite(value) &&
    value > IV_SANE_MIN &&
    value <= IV_SANE_MAX
  );
}

// A finite numeric guard for any analytics scalar (a Greek, a log-moneyness): excludes
// null/undefined/NaN/±∞ so a degenerate cell cannot spike a line panel or a table.
export function isFiniteNumber(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value);
}

export interface CleanSmileResult {
  logMoneyness: number[];
  impliedVols: number[];
  // How many raw points were dropped, split by cause, so the panel can label the degradation
  // honestly ("3 flagged" beside the smile) instead of silently hiding the railed wing.
  nDroppedNonFinite: number;
  nDroppedAbsurd: number;
  nDroppedDuplicate: number;
}

// Clean a single smile slice for plotting: drop points whose IV is non-finite or outside the
// sane band, and collapse exact duplicate log-moneyness points (the duplicated 0.0 delta the
// railed slices carry). Order is preserved; the FIRST occurrence of a duplicate k wins.
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
  // Non-finite / out-of-band IV cells become null (a mesh hole bridged by connectgaps), so a
  // single railed cell can neither spike the surface height nor stretch the colour band.
  impliedVol: (number | null)[][];
  // The count of clamped cells and of slices (maturity rows) that carried at least one — surfaced
  // as an honest "N slices flagged" note on the nappe.
  nFlaggedCells: number;
  nFlaggedSlices: number;
}

// Clean the dense reconstructed surface for the 3D nappe: replace every non-finite / out-of-band
// IV with null (a hole the surface bridges), drop exact-duplicate log-moneyness columns, and
// count how many cells/slices were flagged. The served arrays are never mutated — a fresh,
// cleaned copy is returned.
export function cleanDenseSurface(
  logMoneyness: readonly number[],
  maturityYears: readonly number[],
  impliedVol: ReadonlyArray<ReadonlyArray<number | null>>,
): CleanDenseSurfaceResult {
  // Keep the first occurrence of each log-moneyness column; record which column indices survive.
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

// A short, honest note for a panel header when N points/slices were flagged out of the render.
// Returns null when nothing was flagged (no note shown on a clean day).
export function flaggedNote(count: number, noun: string): string | null {
  if (count <= 0) return null;
  return `${count} ${noun}${count === 1 ? "" : "s"} flagged (excluded from view)`;
}
