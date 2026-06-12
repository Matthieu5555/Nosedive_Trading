// Thin Plotly wrapper so every Plotly chart imports one module and tests mock one path.
//
// Plotly draws to a canvas/WebGL surface that jsdom does not implement, so component tests
// stub this module (see src/test/plotMock.tsx) with a DOM stand-in that exposes the trace
// types and the self-label as text. Production code imports the real react-plotly.js bound to
// the dist-min bundle. Plotly owns the 3D surfaces and heatmaps; compact 2D financial panels
// (candlesticks, term-structure lines, the smile) live on TradingView Lightweight Charts —
// see components/charts.tsx.
//
// Theming rides Plotly's native `layout.template` (see chartTheme.ts): templates merge
// per-attribute, so a caller layout that sets only an axis title keeps the themed
// gridcolor/tickcolor on that same axis.

import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import type { Data, Layout } from "plotly.js";

import { themedPlotLayout } from "./chartTheme";

const PlotlyComponent = createPlotlyComponent(Plotly);

export interface PlotProps {
  data: Data[];
  layout?: Partial<Layout>;
  // A required, human-readable label that answers "what am I looking at?" for every panel.
  label: string;
  // Explicit pixel height. Plotly sizes to its container, but `.plot` has no intrinsic height, so
  // a percentage height collapses to 0 and the chart renders invisible — every Plotly panel must
  // pass (or inherit) a real height. Default suits the 3D surface / heatmaps.
  height?: number;
}

export function Plot({ data, layout, label, height = 440 }: PlotProps) {
  return (
    <figure aria-label={label} className="plot">
      <figcaption>{label}</figcaption>
      <PlotlyComponent
        data={data}
        layout={themedPlotLayout(layout)}
        useResizeHandler
        style={{ width: "100%", height }}
        config={{ displaylogo: false, responsive: true }}
      />
    </figure>
  );
}
