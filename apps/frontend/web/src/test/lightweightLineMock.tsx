// Test stand-in for the TradingView line-chart wrapper. The real component draws to canvas;
// this DOM stub exposes the self-label, series names, unit, and point count to component tests.

import type { LightweightLineChartProps } from "../components/LightweightLineChart";

export function LightweightLineChart({ label, series, yUnit }: LightweightLineChartProps) {
  return (
    <figure aria-label={label}>
      <figcaption>{label}</figcaption>
      <div data-testid="line-series">{series.map((item) => item.label).join(",")}</div>
      <div data-testid="line-points">
        {series.reduce((total, item) => total + item.points.length, 0)}
      </div>
      <div data-testid="line-unit">{yUnit}</div>
    </figure>
  );
}
