import type { AnalyticsPoint, DollarMetric } from "../api";
import { sci, sciUnit, UNITS, withCurrency } from "../lib/format";

const GREEK_ORDER: Array<keyof AnalyticsPoint["metrics"]> = [
  "delta",
  "gamma",
  "vega",
  "theta",
  "rho",
];

const RAW_UNIT: Record<(typeof GREEK_ORDER)[number], string> = {
  delta: UNITS.delta,
  gamma: UNITS.gamma,
  vega: UNITS.vega,
  theta: UNITS.theta,
  rho: UNITS.rho,
};

function formatDollar(metric: DollarMetric): string {
  return sci(metric.dollar);
}

export function DollarGreeks({
  point,
  currency = "$",
}: {
  point: AnalyticsPoint;
  currency?: string;
}) {
  const label = `Dollar Greeks — ${point.delta_band} band`;
  return (
    <table aria-label={label}>
      <caption>{label}</caption>
      <thead>
        <tr>
          <th>Greek</th>
          <th>$ value</th>
          <th>unit</th>
          <th>raw</th>
        </tr>
      </thead>
      <tbody>
        {GREEK_ORDER.map((name) => {
          const metric = point.metrics[name];
          return (
            <tr key={name}>
              <td>{name}</td>
              <td>{formatDollar(metric)}</td>
              {/* The unit string carries "$" as the currency placeholder; render it in the
                  index's real quote currency (€ for SX5E), so the operator sees what the value
                  is per — not a hard-coded dollar (05-math-notes). */}
              <td>{metric.unit ? withCurrency(metric.unit, currency) : "n/a"}</td>
              <td>{sciUnit(metric.raw, withCurrency(RAW_UNIT[name], currency))}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// The per-maturity compact view: ONE matrix (greeks × delta bands) instead of one stacked
// table per band — eight band tables per maturity read as a wall of numbers (the "pages pas
// propres" report). Bands are ordered put → ATM → call (by signed target delta, the smile's
// reading order); each row carries its unit string once, read verbatim from the payload.
export function DollarGreeksMatrix({
  points,
  currency = "$",
}: {
  points: AnalyticsPoint[];
  currency?: string;
}) {
  if (points.length === 0) return null;
  const ordered = [...points].sort((a, b) => a.target_delta - b.target_delta);
  // The row's unit string in the index's real quote currency (€ for SX5E): the stored unit
  // carries "$" as the currency placeholder, substituted here, never re-derived.
  const unitFor = (name: keyof AnalyticsPoint["metrics"]): string => {
    const unit = ordered.map((p) => p.metrics[name].unit).find((u) => u !== null) ?? null;
    return withCurrency(unit, currency) ?? "n/a";
  };
  return (
    <table aria-label="Dollar Greeks by delta band">
      <caption>Dollar Greeks by delta band ($ value; unit per row)</caption>
      <thead>
        <tr>
          <th>Greek</th>
          <th>unit</th>
          {ordered.map((p) => (
            <th key={p.delta_band}>{p.delta_band}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {GREEK_ORDER.map((name) => (
          <tr key={name}>
            <td>{name}</td>
            <td>{unitFor(name)}</td>
            {ordered.map((p) => (
              <td key={p.delta_band}>{formatDollar(p.metrics[name])}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
