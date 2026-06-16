export const IV_SANE_MAX = 0.6;
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
