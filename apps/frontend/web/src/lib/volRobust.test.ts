import { describe, expect, test } from "vitest";

import {
  cleanDenseSurface,
  cleanSmile,
  flaggedNote,
  isFiniteNumber,
  isSaneIv,
  IV_SANE_MAX,
} from "./volRobust";

describe("isSaneIv", () => {
  test("accepts a finite IV inside the sane band", () => {
    expect(isSaneIv(0.16)).toBe(true);
    expect(isSaneIv(IV_SANE_MAX)).toBe(true); // the cap itself is in-band (≤)
  });
  test("rejects non-finite, non-positive, and absurd railed IVs", () => {
    expect(isSaneIv(Number.NaN)).toBe(false);
    expect(isSaneIv(Number.POSITIVE_INFINITY)).toBe(false);
    expect(isSaneIv(0)).toBe(false); // not > 0
    expect(isSaneIv(-0.1)).toBe(false);
    expect(isSaneIv(1.08)).toBe(false); // 108% railed
    expect(isSaneIv(1.4)).toBe(false); // 140% railed
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
  // The live 2026-06-15 SX5E 10d shape, in miniature: two good points, an absurd railed IV
  // (108%), a NaN, a duplicate log-moneyness (the duplicated 0.0 delta), and a good point.
  const ks = [-0.03, -0.18, -0.25, 0.0, 0.0, 0.03];
  const ivs = [0.19, 1.08, Number.NaN, 0.152, 0.152, 0.143];

  test("drops absurd, non-finite, and duplicate-k points; keeps the good ones", () => {
    const r = cleanSmile(ks, ivs);
    // Kept: -0.03 (0.19), 0.0 (0.152, first), 0.03 (0.143). Dropped: -0.18 (absurd),
    // -0.25 (NaN), the second 0.0 (duplicate).
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
});

describe("cleanDenseSurface (railed slice cannot blow the surface)", () => {
  // A 2-maturity surface: the first (short) slice rails to 1.4 in the deep-OTM put wing; the
  // second is clean. Column k=-0.1 is duplicated (must collapse).
  const logMoneyness = [-0.2, -0.1, -0.1, 0.0, 0.1];
  const maturityYears = [0.03, 1.0];
  const impliedVol = [
    [1.4, 0.55, 0.55, 0.15, 0.11], // railed deep-OTM put (1.4 > cap), rest in-band
    [0.24, 0.22, 0.22, 0.21, 0.2],
  ];

  test("clamps out-of-band cells to null holes, collapses duplicate columns, counts flags", () => {
    const r = cleanDenseSurface(logMoneyness, maturityYears, impliedVol);
    // Duplicate -0.1 column collapsed → 4 columns.
    expect(r.logMoneyness).toEqual([-0.2, -0.1, 0.0, 0.1]);
    // The 1.4 cell on the short slice is a null hole; everything else survives in-band.
    expect(r.impliedVol).toEqual([
      [null, 0.55, 0.15, 0.11],
      [0.24, 0.22, 0.21, 0.2],
    ]);
    expect(r.nFlaggedCells).toBe(1);
    expect(r.nFlaggedSlices).toBe(1); // only the short slice carried an out-of-band cell
    // The maximum surviving IV is within the sane band — the colour/height scale cannot be blown.
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
