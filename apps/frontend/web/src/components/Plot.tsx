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
  const defaultLayout: any = {
    autosize: true,
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: {
      color: "#f2f5ef", // matching --text
      family: '"Basis Grotesque", Inter, sans-serif',
      size: 11,
    },
    xaxis: {
      gridcolor: "#2b302c", // matching --border
      linecolor: "#454d45", // matching --border-strong
      tickcolor: "#454d45",
      color: "#8f978f", // matching --muted
      ...layout?.xaxis,
    },
    yaxis: {
      gridcolor: "#2b302c",
      linecolor: "#454d45",
      tickcolor: "#454d45",
      color: "#8f978f",
      ...layout?.yaxis,
    },
    margin: {
      t: 30,
      b: 40,
      l: 50,
      r: 30,
      ...layout?.margin,
    },
  };

  if (layout?.scene) {
    defaultLayout.scene = {
      xaxis: {
        gridcolor: "#2b302c",
        color: "#8f978f",
        backgroundcolor: "rgba(0,0,0,0)",
        showbackground: false,
        ...layout.scene.xaxis,
      },
      yaxis: {
        gridcolor: "#2b302c",
        color: "#8f978f",
        backgroundcolor: "rgba(0,0,0,0)",
        showbackground: false,
        ...layout.scene.yaxis,
      },
      zaxis: {
        gridcolor: "#2b302c",
        color: "#8f978f",
        backgroundcolor: "rgba(0,0,0,0)",
        showbackground: false,
        ...layout.scene.zaxis,
      },
      ...layout.scene,
    };
  }

  const mergedLayout = {
    ...defaultLayout,
    ...layout,
    scene: layout?.scene ? {
      ...defaultLayout.scene,
      ...layout.scene,
    } : undefined,
  };

  return (
    <figure aria-label={label} className="plot">
      <figcaption>{label}</figcaption>
      <PlotlyComponent
        data={data}
        layout={mergedLayout}
        useResizeHandler
        style={{ width: "100%", height: "100%" }}
        config={{ displaylogo: false, responsive: true }}
      />
    </figure>
  );
}
