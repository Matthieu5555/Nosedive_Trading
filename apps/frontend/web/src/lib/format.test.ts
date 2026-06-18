import { describe, expect, it } from "vitest";

import {
  asOfClose,
  count,
  coverageHeadline,
  coveragePercent,
  currencySymbol,
  indexWeightPercent,
  referencePrice,
  sci,
  sciUnit,
  UNITS,
  withCurrency,
} from "./format";

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

describe("count", () => {
  it("renders a cardinality or signed quantity as a plain grouped integer, never sci", () => {
    expect(count(0)).toBe("0");
    expect(count(5)).toBe("5");
    expect(count(-3)).toBe("-3");
    expect(count(1234)).toBe("1,234");
    expect(count(2.0)).toBe("2");
  });

  it("rounds a stray fractional quantity to the nearest whole lot", () => {
    expect(count(2.4)).toBe("2");
    expect(count(-2.6)).toBe("-3");
  });

  it("labels a missing count rather than emitting a bare blank", () => {
    expect(count(null)).toBe("-");
    expect(count(undefined)).toBe("-");
    expect(count(Number.NaN)).toBe("-");
  });
});

describe("indexWeightPercent", () => {
  it("renders a percent-scale weight as a plain grouped percent, never scientific", () => {
    // Real SX5E payload values: weights already sum to ~100, so they ARE the percent.
    expect(indexWeightPercent(12.076038)).toBe("12.08%");
    expect(indexWeightPercent(1.345434)).toBe("1.35%");
    expect(indexWeightPercent(0.597104)).toBe("0.60%");
  });

  it("groups thousands and honours a custom decimal count", () => {
    expect(indexWeightPercent(1234.5)).toBe("1,234.50%");
    expect(indexWeightPercent(12.076038, 1)).toBe("12.1%");
  });

  it("labels a missing weight n/a rather than emitting a bare blank", () => {
    expect(indexWeightPercent(null)).toBe("n/a");
    expect(indexWeightPercent(undefined)).toBe("n/a");
    expect(indexWeightPercent(Number.NaN)).toBe("n/a");
  });
});

describe("referencePrice", () => {
  it("renders a plain grouped amount with the index currency, never scientific", () => {
    // Real SX5E payload closes: ASML 1624.0, RMS 1693.0, ADYEN small.
    expect(referencePrice(1624, "EUR")).toBe("€1,624.00");
    expect(referencePrice(1693, "EUR")).toBe("€1,693.00");
    expect(referencePrice(1199.6, "EUR")).toBe("€1,199.60");
    expect(referencePrice(1624, "USD")).toBe("$1,624.00");
  });

  it("falls back to a plain grouped number with two decimals when currency is absent/unknown", () => {
    expect(referencePrice(1624)).toBe("1,624.00");
    expect(referencePrice(1624, null)).toBe("1,624.00");
    expect(referencePrice(1624, "ZZZ")).toBe("1,624.00");
  });

  it("labels a missing price rather than emitting a bare blank", () => {
    expect(referencePrice(null, "EUR")).toBe("-");
    expect(referencePrice(undefined)).toBe("-");
    expect(referencePrice(Number.NaN, "EUR")).toBe("-");
  });

  it("renders an OHLC candlestick legend as plain prices, never scientific (the SIE bug)", () => {
    // The reported bug: SIE daily OHLC rendered "2.654 × 10²" etc. for ordinary ~264 stock prices.
    // The candlestick legend routes O/H/L/C through referencePrice; with no currency on the bar
    // payload it must read as a plain grouped price.
    expect(referencePrice(264)).toBe("264.00");
    expect(referencePrice(269.74)).toBe("269.74");
    expect(referencePrice(262.34)).toBe("262.34");
    // Independently derived: 264 -> "264.00", 1624 -> "1,624.00", 1234567.5 -> "1,234,567.50".
    expect(referencePrice(1624)).toBe("1,624.00");
    expect(referencePrice(1234567.5)).toBe("1,234,567.50");
    // With a currency available it carries the symbol but stays plain (not scientific).
    expect(referencePrice(264, "EUR")).toBe("€264.00");
  });
});

describe("currencySymbol", () => {
  it("maps ISO codes to symbols, defaulting/falling back sensibly", () => {
    expect(currencySymbol("EUR")).toBe("€");
    expect(currencySymbol("USD")).toBe("$");
    expect(currencySymbol("GBP")).toBe("£");
    expect(currencySymbol("SEK")).toBe("SEK");
    expect(currencySymbol(null)).toBe("$");
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
    expect(withCurrency(UNITS.vol, "€")).toBe("Vol");
  });

  it("passes null/undefined units through unchanged", () => {
    expect(withCurrency(null, "€")).toBeNull();
    expect(withCurrency(undefined, "€")).toBeUndefined();
  });
});

describe("coveragePercent", () => {
  it("reports the two-sided fraction in PM register (fr-FR, comma decimal)", () => {
    expect(coveragePercent({ twoSided: 1706, total: 2412 })).toBe(`70.7%`);
    expect(coveragePercent({ twoSided: 2412, total: 2412 })).toBe(`100.0%`);
  });

  it("labels an empty chain n/a rather than dividing by zero", () => {
    expect(coveragePercent({ twoSided: 0, total: 0 })).toBe("n/a");
  });
});

describe("coverageHeadline", () => {
  it("names the captured-chain fraction and the excluded one-sided count", () => {
    expect(coverageHeadline({ twoSided: 1706, total: 2412 })).toBe(
      `1,706 / 2,412 quotes · 70.7% two-sided · 706 one-sided excluded`,
    );
  });

  it("recedes to full coverage when nothing is excluded", () => {
    expect(coverageHeadline({ twoSided: 2412, total: 2412 })).toBe(
      `2,412 / 2,412 quotes · 100.0% two-sided · full coverage`,
    );
  });
});

describe("asOfClose", () => {
  it("renders the as-of with the BFF-resolved close instant (threaded, not a front-side map)", () => {
    expect(asOfClose("2026-06-17", "17:30 CET")).toBe("close 2026-06-17 17:30 CET");
  });

  it("falls back to a bare close date when no instant was resolved", () => {
    expect(asOfClose("2026-06-17", null)).toBe("close 2026-06-17");
    expect(asOfClose("2026-06-17")).toBe("close 2026-06-17");
  });

  it("never invents a date, an absent as-of is labelled, not blank", () => {
    expect(asOfClose(null, "17:30 CET")).toBe("date unresolved");
  });
});
