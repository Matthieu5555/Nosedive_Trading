// The dollar-Greek panel: each metric with its unit string visible (P0.2 / ADR 0036).
//
// The unit string is read straight from the BFF payload — it is never re-derived here. An
// older partition can carry a null dollar/unit (the field predates that partition); we render
// a labeled "n/a" rather than a bare blank so the absence is explicit.

import type { AnalyticsPoint, DollarMetric } from "../api";

const GREEK_ORDER: Array<keyof AnalyticsPoint["metrics"]> = [
  "delta",
  "gamma",
  "vega",
  "theta",
  "rho",
];

function formatDollar(metric: DollarMetric): string {
  if (metric.dollar === null) return "n/a";
  return metric.dollar.toFixed(4);
}

export function DollarGreeks({ point }: { point: AnalyticsPoint }) {
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
              {/* The unit string is rendered verbatim so the operator sees what the $ is per. */}
              <td>{metric.unit ?? "n/a"}</td>
              <td>{metric.raw.toFixed(6)}</td>
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
export function DollarGreeksMatrix({ points }: { points: AnalyticsPoint[] }) {
  if (points.length === 0) return null;
  const ordered = [...points].sort((a, b) => a.target_delta - b.target_delta);
  const unitFor = (name: keyof AnalyticsPoint["metrics"]): string =>
    ordered.map((p) => p.metrics[name].unit).find((u) => u !== null) ?? "n/a";
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
