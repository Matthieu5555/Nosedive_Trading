import type { CandleChartProps } from "../components/CandleChart";

export function CandleChart({ bars, label }: CandleChartProps) {
  return (
    <figure aria-label={label}>
      <figcaption>{label}</figcaption>
      <div data-testid="candle-bars">{bars.length}</div>
    </figure>
  );
}
