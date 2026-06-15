// Display formatters — restored from Antho's demo so every panel renders numbers the way
// operators read them (currency, signed PnL, percentages, fractional vol as a percent).
//
// House rule (owner ruling 2026-06-15): every quantitative analytics number on screen is
// rendered in SCIENTIFIC NOTATION at six significant figures with trailing zeros stripped,
// and never without its unit. `sci`/`sciUnit` below are the single home of that rule; the
// `UNITS` vocabulary is the single home of the unit tokens. Pure cardinalities (counts),
// dates, ids, and enum labels are not analytics quantities and keep their plain rendering.

// Unicode superscript digits + minus, so an exponent renders as "× 10⁻¹" inline anywhere a
// string goes (table cell, chart axis, SVG tooltip) without needing JSX <sup>.
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

/**
 * Scientific notation at `sigFigs` significant figures (default six) with trailing zeros
 * stripped: 0.58 → "5.8 × 10⁻¹", 0.032 → "3.2 × 10⁻²", 12.5 → "1.25 × 10¹",
 * 0.123456789 → "1.23457 × 10⁻¹". Zero is "0" (scientific notation of zero is just zero);
 * a non-finite or missing value is the labelled "n/a"/"∞" rather than a bare blank.
 */
export function sci(value: number | null | undefined, sigFigs = 6): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  if (!Number.isFinite(value)) return value > 0 ? "∞" : "−∞";
  if (value === 0) return "0";
  // toExponential(sigFigs-1) gives exactly `sigFigs` significant figures with padding zeros;
  // strip the trailing zeros (and a now-dangling decimal point) from the mantissa.
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

/** API enums ("paper_accepted") rendered as labels ("Paper accepted"). */
export function statusLabel(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
