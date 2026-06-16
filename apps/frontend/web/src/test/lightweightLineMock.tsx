import type { LightweightLineChartProps } from "../components/LightweightLineChart";

export function LightweightLineChart({ label, series, yUnit }: LightweightLineChartProps) {
  return (
    <figure aria-label={label}>
      <figcaption>{label}</figcaption>
      <div data-testid="line-series">{series.map((item) => item.label).join(",")}</div>
      <div data-testid="line-points">
        {series.reduce((total, item) => total + item.points.length, 0)}
      </div>
      {}
      <div data-testid="line-values">
        {JSON.stringify(series.map((item) => item.points.map((point) => point.value)))}
      </div>
      <div data-testid="line-unit">{yUnit}</div>
    </figure>
  );
}
