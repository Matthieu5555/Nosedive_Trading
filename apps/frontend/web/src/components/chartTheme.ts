// The dark-panel chart theme, in one place. The hex values mirror the CSS custom properties
// in src/index.css (the design-token source of truth); charts cannot read CSS variables at
// canvas/WebGL draw time, so this module is the single TS home for them — never copy the hex
// into a chart component again (a test pins this file against index.css).
//
// Two consumers:
//   - Plotly panels go through `themedPlotLayout`, which attaches the theme as a native
//     `layout.template`. Plotly templates merge per-attribute by design, so a caller-supplied
//     axis (e.g. an axis title) never clobbers the themed gridcolor/tickcolor — the exact bug
//     the old hand-rolled spread-merge had.
//   - TradingView Lightweight Charts panels spread `baseLightweightOptions()` into their
//     create-chart options and override only what is panel-specific.

import { ColorType, CrosshairMode } from "lightweight-charts";
import type { Layout, Template } from "plotly.js";

export const CHART_COLORS = {
  grid: "#2b302c", // --border
  axis: "#454d45", // --border-strong
  text: "#f2f5ef", // --text
  muted: "#8f978f", // --muted
  positive: "#a8e6ba", // --positive
  negative: "#ef9c92", // --negative
  transparent: "rgba(0,0,0,0)",
} as const;

export const CHART_FONT_FAMILY = '"Basis Grotesque", Inter, sans-serif';
export const CHART_FONT_SIZE = 11;

const PLOT_AXIS_THEME = {
  gridcolor: CHART_COLORS.grid,
  linecolor: CHART_COLORS.axis,
  tickcolor: CHART_COLORS.axis,
  color: CHART_COLORS.muted,
};

const PLOT_SCENE_AXIS_THEME = {
  gridcolor: CHART_COLORS.grid,
  color: CHART_COLORS.muted,
  backgroundcolor: CHART_COLORS.transparent,
  showbackground: false,
};

// The whole theme as a Plotly template: per-attribute defaults that any caller layout merges
// over without losing the rest of the themed attribute group.
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
    scene: {
      xaxis: PLOT_SCENE_AXIS_THEME,
      yaxis: PLOT_SCENE_AXIS_THEME,
      zaxis: PLOT_SCENE_AXIS_THEME,
    },
  },
};

// A caller layout themed via the template — the caller's own attributes always win, the theme
// fills every attribute the caller does not set.
export function themedPlotLayout(layout?: Partial<Layout>): Partial<Layout> {
  return { autosize: true, ...layout, template: PLOTLY_TEMPLATE };
}

// The shared create-chart options for every TradingView Lightweight Charts panel; callers
// spread this and add panel-specific options (time scale, localization, yield-curve axis).
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
