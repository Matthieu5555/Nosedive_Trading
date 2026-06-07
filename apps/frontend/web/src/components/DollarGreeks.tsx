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
