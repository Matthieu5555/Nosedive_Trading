import { ColorType, CrosshairMode } from "lightweight-charts";
import type { Layout, Template } from "plotly.js";

export const CHART_COLORS = {
  grid: "#2b302c",
  axis: "#454d45",
  text: "#f2f5ef",
  muted: "#8f978f",
  positive: "#a8e6ba",
  negative: "#ef9c92",
  transparent: "rgba(0,0,0,0)",
} as const;

// The single UI font stack, kept byte-identical to the `:root` font-family in src/index.css so
// canvas-drawn chart text resolves to the exact same font as the surrounding HTML. No web font is
// loaded, so a divergent fallback chain here vs. the CSS is what made charts render in a different
// fallback than the page — keep these two in lockstep.
export const CHART_FONT_FAMILY =
  'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';
export const CHART_FONT_SIZE = 11;

export const VOL_COLORSCALE = "Plasma" as const;

const PLOT_AXIS_THEME = {
  gridcolor: CHART_COLORS.grid,
  linecolor: CHART_COLORS.axis,
  tickcolor: CHART_COLORS.axis,
  color: CHART_COLORS.muted,
  // Scientific tick labels (".2e") are wide; automargin grows the axis gutter to fit them so the
  // axis TITLE never lands on top of the tick text (the "ATM implied vol" / "3.00e-1" collision).
  automargin: true,
};

const PLOT_SCENE_AXIS_THEME = {
  gridcolor: CHART_COLORS.grid,
  color: CHART_COLORS.muted,
  backgroundcolor: CHART_COLORS.transparent,
  showbackground: false,
};

export const PLOTLY_TEMPLATE: Template = {
  layout: {
    paper_bgcolor: CHART_COLORS.transparent,
    plot_bgcolor: CHART_COLORS.transparent,
    font: {
      color: CHART_COLORS.text,
      family: CHART_FONT_FAMILY,
      size: CHART_FONT_SIZE,
    },
    xaxis: PLOT_AXIS_THEME,
    yaxis: PLOT_AXIS_THEME,
    margin: { t: 30, b: 40, l: 50, r: 30 },
    // Plotly's default legend background is opaque WHITE — a glaring box over the dark panels.
    // Make it transparent with muted text so it reads as part of the console, not a paste-over.
    legend: {
      bgcolor: CHART_COLORS.transparent,
      bordercolor: CHART_COLORS.transparent,
      font: { color: CHART_COLORS.muted },
    },
    scene: {
      xaxis: PLOT_SCENE_AXIS_THEME,
      yaxis: PLOT_SCENE_AXIS_THEME,
      zaxis: PLOT_SCENE_AXIS_THEME,
    },
  },
};

export function themedPlotLayout(layout?: Partial<Layout>): Partial<Layout> {
  return { autosize: true, ...layout, template: PLOTLY_TEMPLATE };
}

export function baseLightweightOptions() {
  return {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: CHART_COLORS.transparent },
      textColor: CHART_COLORS.muted,
      fontFamily: CHART_FONT_FAMILY,
      fontSize: CHART_FONT_SIZE,
    },
    grid: {
      vertLines: { color: CHART_COLORS.grid },
      horzLines: { color: CHART_COLORS.grid },
    },
    rightPriceScale: { borderColor: CHART_COLORS.axis },
    crosshair: { mode: CrosshairMode.Normal },
  };
}
