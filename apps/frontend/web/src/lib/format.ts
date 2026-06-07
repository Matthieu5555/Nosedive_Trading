// Display formatters — restored from Antho's demo so every panel renders numbers the way
// operators read them (currency, signed PnL, percentages, fractional vol as a percent).

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
