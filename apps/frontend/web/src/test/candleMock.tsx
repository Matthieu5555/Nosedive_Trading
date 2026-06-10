// Test stand-in for the lightweight-charts candlestick wrapper. lightweight-charts draws to a
// <canvas> jsdom does not implement, so component tests mock "../components/CandleChart" with
// this DOM stub: it renders the self-label (so the "every panel self-labels" assertion holds)
// and the bar count as text (so a test can assert the candlestick was fed its data) without a
// real canvas — mirroring src/test/plotMock.tsx for the Plotly wrapper.

import type { CandleChartProps } from "../components/CandleChart";

export function CandleChart({ bars, label }: CandleChartProps) {
  return (
    <figure aria-label={label}>
      <figcaption>{label}</figcaption>
      <div data-testid="candle-bars">{bars.length}</div>
    </figure>
  );
}
