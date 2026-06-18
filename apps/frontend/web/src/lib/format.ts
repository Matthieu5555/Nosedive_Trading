const SUPERSCRIPT: Record<string, string> = {
  "0": "⁰",
  "1": "¹",
  "2": "²",
  "3": "³",
  "4": "⁴",
  "5": "⁵",
  "6": "⁶",
  "7": "⁷",
  "8": "⁸",
  "9": "⁹",
  "-": "⁻",
};

function superscript(exponent: number): string {
  return String(exponent)
    .split("")
    .map((ch) => SUPERSCRIPT[ch] ?? ch)
    .join("");
}

export function sci(value: number | null | undefined, sigFigs = 6): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  if (!Number.isFinite(value)) return value > 0 ? "∞" : "−∞";
  if (value === 0) return "0";

  const [mantissaRaw, expRaw] = value.toExponential(sigFigs - 1).split("e");
  const mantissa = mantissaRaw.includes(".")
    ? mantissaRaw.replace(/0+$/, "").replace(/\.$/, "")
    : mantissaRaw;
  return `${mantissa} × 10${superscript(Number(expRaw))}`;
}

/**
 * A quantitative number with its unit, the only way an analytics value should reach the
 * screen: "5.8 × 10⁻¹ $/$". A null/undefined value is the labelled "n/a" (still no bare
 * blank), and a null/undefined unit falls back to the number alone rather than appending
 * "undefined".
 */
export function sciUnit(
  value: number | null | undefined,
  unit: string | null | undefined,
  sigFigs = 6,
): string {
  const rendered = sci(value, sigFigs);
  if (value === null || value === undefined) return rendered;
  return unit ? `${rendered} ${unit}` : rendered;
}

/**
 * The canonical unit vocabulary (owner notation, 2026-06-15). The raw-Greek mathematical
 * units and the units of every other naked analytics number, so the front never invents a
 * unit per component. "Vol" is one full unit of annualised vol (1.00 = 100%); "Time(y)" is
 * years. Config-forked $-Greek units (e.g. "$ per 1% move") are NOT here — those are pinned
 * by ADR 0036 and travel from the backend on the metric, never re-derived on the front.
 */
export const UNITS = {
  // Raw first-order Greeks (∂Price/∂x), in the owner's notation.
  delta: "$/$",
  gamma: "1/$",
  vega: "$/Vol",
  theta: "$/Time(y)",
  rho: "$/Rate",
  // Second-order set.
  vanna: "1/Vol",
  volga: "$/Vol²",
  charm: "$/(Time(y)·$)",
  rt_vega: "$/Vol",
  // Prices / money.
  price: "$",
  pnl: "$",
  // Vol space.
  vol: "Vol",
  variance: "Vol²·y",
  // Geometry of the chain.
  strike: "$",
  forward: "$",
  logMoneyness: "ln(K/F)",
  moneyness: "ln(K/F)",
  years: "y",
  // SVI raw parameters (total-variance parametrisation).
  sviA: "Vol²·y",
  sviB: "Vol²·y",
  sviRho: "(ratio)",
  sviM: "ln(K/F)",
  sviSigma: "ln(K/F)",
  rmse: "Vol²·y",
  // Shocks / weights / rates as fractions.
  shock: "(frac)",
  weight: "(frac)",
  rate: "(frac)",
  // Share quantities.
  shares: "sh",
} as const;

// Currency symbol for an ISO code (the index's quote currency from /api/indices). The
// blueprint requires monetized Greeks/PnL in the *correct* currency (05-math-notes), driven
// by the registry, never a hard-coded "$". Unknown codes fall back to the code itself.
const CURRENCY_SYMBOL: Record<string, string> = {
  USD: "$",
  EUR: "€",
  GBP: "£",
  JPY: "¥",
  CHF: "Fr",
};

export function currencySymbol(code: string | null | undefined): string {
  if (!code) return "$";
  return CURRENCY_SYMBOL[code] ?? code;
}

/**
 * Re-currency a unit string: both the `UNITS` tokens (`$/$`, `$/Vol`, …) and the backend
 * `$`-Greek unit strings (`"$ per 1% move"`, `"$ per $1 of underlying"`) carry `$` as the
 * currency placeholder, so substituting it is the single way to render the right currency on
 * the front (the stored unit strings still say `$` — a legacy contract artifact; the front
 * renders the real currency from the payload, no re-capture). A missing symbol or plain `$`
 * leaves the unit untouched, so a USD/unknown-currency view is unchanged.
 */
export function withCurrency(
  unit: string | null | undefined,
  symbol: string | null | undefined,
): string | null | undefined {
  if (unit === null || unit === undefined) return unit;
  if (!symbol || symbol === "$") return unit;
  return unit.replaceAll("$", symbol);
}

export function enFraction(value: number, digits = 1): string {
  return value.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function enInteger(value: number): string {
  return Math.round(value).toLocaleString("en-US");
}

/**
 * A cardinality or signed lot quantity (number of fills, match/break counts, contracts held,
 * a reconciliation diff): a plain grouped integer with its sign kept, never the 6-sig-fig
 * scientific form analytics values get. These numbers do not span orders of magnitude, so
 * "5 × 10⁰" is noise; "5" (or "-2" for a short) is the honest read. Null/undefined → "-".
 */
export function count(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return Math.round(value).toLocaleString("en-US");
}

export interface CoverageCounts {
  twoSided: number;
  total: number;
}

export function coveragePercent(coverage: CoverageCounts, digits = 1): string {
  if (coverage.total <= 0) return "n/a";
  return `${enFraction((coverage.twoSided / coverage.total) * 100, digits)}%`;
}

export function coverageHeadline(coverage: CoverageCounts): string {
  const excluded = Math.max(coverage.total - coverage.twoSided, 0);
  const base = `${enInteger(coverage.twoSided)} / ${enInteger(coverage.total)} quotes · ${coveragePercent(coverage)} two-sided`;
  if (excluded <= 0) return `${base} · full coverage`;
  return `${base} · ${enInteger(excluded)} one-sided excluded`;
}

// The close instant is never a front-side constant: it is resolved server-side from the index
// registry (the BFF /api/analytics `close_instant`, venue time-of-day + zone) and threaded in. A
// caller that has it passes it; absent → a date-only as-of, never a guessed instant.
export function asOfClose(asOf: string | null | undefined, closeInstant?: string | null): string {
  if (!asOf) return "date unresolved";
  return closeInstant ? `close ${asOf} ${closeInstant}` : `close ${asOf}`;
}

export function number(value: number, digits = 2): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: digits });
}

export function money(value: number, currency = "USD", digits = 2): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

/** Signed money for PnL-like values: "+$1,234", "-$915"; zero stays unsigned. */
export function signedMoney(value: number, currency = "USD", digits = 0): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    signDisplay: "exceptZero",
  }).format(value);
}

export function percent(value: number, digits = 2): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

/** Fractional vol (0.165) shown the way operators read it: "16.5%". */
export function volPercent(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}

/**
 * An index-weight figure for the constituents table. The backend already sends weights on a
 * percent scale (the 50 SX5E members sum to ~100, e.g. ASML 12.076038), so this is a plain
 * grouped percent at two decimals: 12.076038 → "12.08%". These are human-reference quantities,
 * not analytics outputs, so they never take the scientific form the greeks get (owner override,
 * 2026-06-18). A null/undefined weight reads "n/a" rather than a bare blank.
 */
export function indexWeightPercent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  return `${value.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}%`;
}

/**
 * A reference share price for the constituents table: a plain grouped amount at two decimals with
 * the index's quote currency, e.g. 1624 → "€1,624.00". A human-read price, never scientific (owner
 * override, 2026-06-18). The currency is an ISO code (EUR/USD/…); an unknown/absent code falls back
 * to a plain grouped number with no symbol. A null/undefined price reads "-".
 */
export function referencePrice(
  value: number | null | undefined,
  currency?: string | null,
  digits = 2,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  if (currency && CURRENCY_SYMBOL[currency]) {
    return money(value, currency, digits);
  }
  return value.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** API enums ("paper_accepted") rendered as labels ("Paper accepted"). */
export function statusLabel(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
