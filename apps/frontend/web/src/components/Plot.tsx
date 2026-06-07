// Thin Plotly wrapper so every chart imports one module and tests mock one path.
//
// Plotly draws to a canvas/WebGL surface that jsdom does not implement, so component tests
// stub this module (see src/test/plotMock.tsx) with a DOM stand-in that exposes the trace
// types and the self-label as text. Production code imports the real react-plotly.js bound to
// the dist-min bundle (ADR 0030: Plotly is the single charting dependency).

import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import type { Data, Layout } from "plotly.js";

const PlotlyComponent = createPlotlyComponent(Plotly);

export interface PlotProps {
  data: Data[];
  layout?: Partial<Layout>;
  // A required, human-readable label that answers "what am I looking at?" for every panel.
  label: string;
}

export function Plot({ data, layout, label }: PlotProps) {
  return (
    <figure aria-label={label} className="plot">
      <figcaption>{label}</figcaption>
      <PlotlyComponent
        data={data}
        layout={{ autosize: true, ...layout }}
        useResizeHandler
        style={{ width: "100%", height: "100%" }}
        config={{ displaylogo: false, responsive: true }}
      />
    </figure>
  );
}
