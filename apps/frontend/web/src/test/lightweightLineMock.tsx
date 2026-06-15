// Test stand-in for the TradingView line-chart wrapper. The real component draws to canvas;
// this DOM stub exposes the self-label, series names, unit, point count, and plotted values to
// component tests.

import type { LightweightLineChartProps } from "../components/LightweightLineChart";

export function LightweightLineChart({ label, series, yUnit }: LightweightLineChartProps) {
  return (
    <figure aria-label={label}>
      <figcaption>{label}</figcaption>
      <div data-testid="line-series">{series.map((item) => item.label).join(",")}</div>
      <div data-testid="line-points">
        {series.reduce((total, item) => total + item.points.length, 0)}
      </div>
      {/* Per-series plotted values, so a test can assert WHICH numbers reached the chart (e.g. the
          ATM IVs read off the surface), not just how many points. */}
      <div data-testid="line-values">
        {JSON.stringify(series.map((item) => item.points.map((point) => point.value)))}
      </div>
      <div data-testid="line-unit">{yUnit}</div>
    </figure>
  );
}
