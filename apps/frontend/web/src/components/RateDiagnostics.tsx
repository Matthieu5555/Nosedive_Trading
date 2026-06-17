import type { RateDiagnostics } from "../api";
import { number } from "../lib/format";

// The interest-rate split, per tenor (core-explicit-rate-config A6, TARGET R1 / blueprint Eq 5):
// the forward F(T), the interest rate r(T) the carry split uses, and the implied carry/dividend
// q(T) = r(T) − ln(F/S)/T it backs out. r is an explicit, displayed input — the owner's ask: "on
// sous-entend un IR fixe mais ça doit rester un paramètre modifiable et donc être explicitement
// affiché." Rates are annualized continuous fractions; they render as percentages carrying the
// BFF's rate_unit, the forward as a plain price in the index currency. A null field is the honest
// "—" (the diagnostic wasn't computed), never a fabricated rate.

// A rate fraction (0.025) as an annualized percentage with its unit ("2.500% /yr (…)"). The unit
// travels from the BFF on `rate_unit`; null reads "—".
function ratePercent(value: number | null, unit: string): string {
  if (value === null || !Number.isFinite(value)) return "-";
  return `${(value * 100).toFixed(3)}% ${unit}`;
}

function forwardPrice(value: number | null, currency: string): string {
  if (value === null || !Number.isFinite(value)) return `, ${currency}`;
  return `${number(value, 2)} ${currency}`;
}

export function RateDiagnosticsPanel({
  diagnostics,
  maturityLabel,
  currency = "$",
}: {
  diagnostics?: RateDiagnostics | null;
  maturityLabel: string;
  currency?: string;
}) {
  if (!diagnostics) {
    return (
      <section aria-label="Rate diagnostics" className="rate-diagnostics">
        <h3>Rate diagnostics, {maturityLabel}</h3>
        <p className="panel-note" role="status">
          No forward/rate diagnostic banked for this tenor (projection gap).
        </p>
      </section>
    );
  }

  const unit = diagnostics.rate_unit;
  const rows: Array<{ label: string; value: string; hint: string }> = [
    {
      label: "Forward",
      value: forwardPrice(diagnostics.forward_price, currency),
      hint: "PCP forward F(T), backed out of put–call parity",
    },
    {
      label: "Interest rate r(T)",
      value: ratePercent(diagnostics.implied_rate, unit),
      hint: "the rate the carry split uses (explicit config rate, else parity-implied)",
    },
    {
      label: "Implied carry q(T)",
      value: ratePercent(diagnostics.implied_carry, unit),
      hint: "q = r − ln(F/S)/T, the rate-adjusted cost of carry",
    },
    {
      label: "Implied dividend",
      value: ratePercent(diagnostics.implied_dividend, unit),
      hint: "the dividend half of the carry split, given r(T)",
    },
  ];

  return (
    <section aria-label="Rate diagnostics" className="rate-diagnostics">
      <div className="price-structure-heading">
        <h3>Rate diagnostics, {maturityLabel}</h3>
        <p className="panel-note">
          The interest rate is an explicit input, not an implicit constant: r(T) drives the forward
          and splits the carry into rate vs dividend (blueprint Eq 5).
        </p>
      </div>
      <dl className="rate-diagnostics__grid">
        {rows.map((row) => (
          <div key={row.label} className="rate-diagnostics__row">
            <dt>{row.label}</dt>
            <dd>{row.value}</dd>
            <dd className="rate-diagnostics__hint">{row.hint}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
