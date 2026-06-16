import type { Data, Layout } from "plotly.js";
import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";

import { themedPlotLayout } from "./chartTheme";

const PlotlyComponent = createPlotlyComponent(Plotly);

export interface PlotProps {
  data: Data[];
  layout?: Partial<Layout>;

  label: string;

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
