// Test stand-in for the Plotly wrapper. Plotly draws to a canvas/WebGL surface jsdom does not
// implement, so component tests mock "../components/Plot" with this DOM stub: it renders the
// self-label (so the "every panel self-labels" assertion holds) and the trace types as text (so
// a test can assert a candlestick / mesh3d / scatter trace was requested) without a real canvas.

import type { PlotProps } from "../components/Plot";

export function Plot({ data, label }: PlotProps) {
  const types = data.map((trace) => (trace as { type?: string }).type ?? "unknown").join(",");
  return (
    <figure aria-label={label}>
      <figcaption>{label}</figcaption>
      <div data-testid="plot-types">{types}</div>
    </figure>
  );
}
