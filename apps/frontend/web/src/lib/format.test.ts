import { describe, expect, it } from "vitest";

import { currencySymbol, sci, sciUnit, UNITS, withCurrency } from "./format";

// Expected strings are derived by hand from the rule (six significant figures, scientific
// notation, trailing zeros stripped, Unicode-superscript exponent), never copied from the
// implementation. The owner's worked example is the anchor: 0.58 → 5.8 × 10⁻¹, etc.
describe("sci", () => {
  it("renders the owner's worked example at 6 sig figs with trailing zeros stripped", () => {
    expect(sci(0.58)).toBe("5.8 × 10⁻¹");
    expect(sci(0.032)).toBe("3.2 × 10⁻²");
    expect(sci(12.5)).toBe("1.25 × 10¹");
    expect(sci(-4.7)).toBe("-4.7 × 10⁰");
    expect(sci(8.4)).toBe("8.4 × 10⁰");
    expect(sci(0.19)).toBe("1.9 × 10⁻¹");
    expect(sci(26)).toBe("2.6 × 10¹");
  });

  it("keeps up to six significant figures, rounding the seventh", () => {
    expect(sci(0.123456789)).toBe("1.23457 × 10⁻¹");
    expect(sci(123456)).toBe("1.23456 × 10⁵");
    expect(sci(123456.7)).toBe("1.23457 × 10⁵");
  });

  it("strips a fully-zero mantissa tail down to the integer digit", () => {
    expect(sci(1_000_000)).toBe("1 × 10⁶");
    expect(sci(200)).toBe("2 × 10²");
  });

  it("handles tiny and negative magnitudes", () => {
    expect(sci(-0.000041)).toBe("-4.1 × 10⁻⁵");
    expect(sci(3.14e-7)).toBe("3.14 × 10⁻⁷");
  });

  it("labels zero, missing, and non-finite values rather than emitting a bare blank", () => {
    expect(sci(0)).toBe("0");
    expect(sci(null)).toBe("n/a");
    expect(sci(undefined)).toBe("n/a");
    expect(sci(Number.NaN)).toBe("n/a");
    expect(sci(Number.POSITIVE_INFINITY)).toBe("∞");
    expect(sci(Number.NEGATIVE_INFINITY)).toBe("−∞");
  });

  it("honours a non-default significant-figure count", () => {
    expect(sci(0.123456789, 3)).toBe("1.23 × 10⁻¹");
  });
});

describe("sciUnit", () => {
  it("appends the unit to the scientific value", () => {
    expect(sciUnit(0.58, UNITS.delta)).toBe("5.8 × 10⁻¹ $/$");
    expect(sciUnit(12.5, UNITS.vega)).toBe("1.25 × 10¹ $/Vol");
    expect(sciUnit(0.032, UNITS.gamma)).toBe("3.2 × 10⁻² 1/$");
  });

  it("labels a missing value as n/a without a dangling unit", () => {
    expect(sciUnit(null, UNITS.delta)).toBe("n/a");
    expect(sciUnit(undefined, UNITS.delta)).toBe("n/a");
  });

  it("falls back to the bare number when no unit is supplied", () => {
    expect(sciUnit(12.5, null)).toBe("1.25 × 10¹");
    expect(sciUnit(12.5, undefined)).toBe("1.25 × 10¹");
  });
});

describe("currencySymbol", () => {
  it("maps ISO codes to symbols, defaulting/falling back sensibly", () => {
    expect(currencySymbol("EUR")).toBe("€");
    expect(currencySymbol("USD")).toBe("$");
    expect(currencySymbol("GBP")).toBe("£");
    expect(currencySymbol("SEK")).toBe("SEK"); // unknown → the code itself
    expect(currencySymbol(null)).toBe("$"); // missing → USD default
    expect(currencySymbol(undefined)).toBe("$");
  });
});

describe("withCurrency", () => {
  it("substitutes the currency symbol for the $ placeholder in unit strings", () => {
    expect(withCurrency(UNITS.delta, "€")).toBe("€/€");
    expect(withCurrency(UNITS.vega, "€")).toBe("€/Vol");
    expect(withCurrency("$ per 1% move", "€")).toBe("€ per 1% move");
    expect(withCurrency("$ per $1 of underlying", "€")).toBe("€ per €1 of underlying");
  });

  it("leaves a unit untouched for USD, a missing symbol, or a unit with no $", () => {
    expect(withCurrency(UNITS.delta, "$")).toBe("$/$");
    expect(withCurrency(UNITS.delta, null)).toBe("$/$");
    expect(withCurrency(UNITS.delta, undefined)).toBe("$/$");
    expect(withCurrency(UNITS.vol, "€")).toBe("Vol"); // no $ to replace
  });

  it("passes null/undefined units through unchanged", () => {
    expect(withCurrency(null, "€")).toBeNull();
    expect(withCurrency(undefined, "€")).toBeUndefined();
  });
});
