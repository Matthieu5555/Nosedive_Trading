// The shared chart theme (M34): the TS tokens must mirror the CSS custom properties in
// src/index.css (the design source of truth), and the Plotly theming must survive a caller
// layout that sets its own axis attributes — the regression the old spread-merge had, where
// a caller axis title silently discarded the themed gridcolor/tickcolor on that axis.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "vitest";

import {
  CHART_COLORS,
  PLOTLY_TEMPLATE,
  baseLightweightOptions,
  themedPlotLayout,
} from "./chartTheme";

// Read the token straight out of index.css, so the expectation is independent of this module:
// if a designer retunes --border in CSS, this test forces the TS mirror to follow. (Read via
// fs — vitest runs with cwd at the package root — because vitest's CSS pipeline swallows both
// `?raw` imports and file:// module URLs for .css files.)
const indexCss = readFileSync(join(process.cwd(), "src", "index.css"), "utf8");

function cssToken(name: string): string {
  const match = indexCss.match(new RegExp(`--${name}:\\s*([^;]+);`));
  if (match === null) throw new Error(`token --${name} not found in index.css`);
  return match[1].trim();
}

test.each([
  ["grid", "border"],
  ["axis", "border-strong"],
  ["text", "text"],
  ["muted", "muted"],
  ["positive", "positive"],
  ["negative", "negative"],
] as const)("CHART_COLORS.%s mirrors the index.css --%s token", (tsName, cssName) => {
  expect(CHART_COLORS[tsName]).toBe(cssToken(cssName));
});

test("a caller axis title no longer clobbers the axis theme (template carries it)", () => {
  // The StressSurface heatmap case: the caller sets only xaxis/yaxis titles. The theme must
  // ride layout.template (merged per-attribute by Plotly), NOT the caller's axis objects —
  // so the title survives and the themed gridcolor is still declared.
  const layout = themedPlotLayout({
    xaxis: { title: { text: "vol shock (additive, vol pts)" } },
    yaxis: { title: { text: "spot shock (relative)" } },
  });

  expect(layout.xaxis).toEqual({ title: { text: "vol shock (additive, vol pts)" } });
  expect(layout.yaxis).toEqual({ title: { text: "spot shock (relative)" } });
  expect(layout.template).toBe(PLOTLY_TEMPLATE);
  expect(layout.template?.layout?.xaxis?.gridcolor).toBe(cssToken("border"));
  expect(layout.template?.layout?.xaxis?.tickcolor).toBe(cssToken("border-strong"));
  expect(layout.template?.layout?.yaxis?.gridcolor).toBe(cssToken("border"));
});

test("a caller 3D scene keeps its axis titles while the template themes the scene axes", () => {
  const layout = themedPlotLayout({
    scene: { xaxis: { title: { text: "delta" } }, aspectmode: "cube" },
  });

  expect(layout.scene?.xaxis).toEqual({ title: { text: "delta" } });
  expect(layout.scene?.aspectmode).toBe("cube");
  expect(layout.template?.layout?.scene?.xaxis?.gridcolor).toBe(cssToken("border"));
  expect(layout.template?.layout?.scene?.zaxis?.showbackground).toBe(false);
});

test("themedPlotLayout without a caller layout still autosizes and carries the template", () => {
  const layout = themedPlotLayout();
  expect(layout.autosize).toBe(true);
  expect(layout.template).toBe(PLOTLY_TEMPLATE);
});

test("baseLightweightOptions reads the same css tokens for grid, axis, and text", () => {
  const options = baseLightweightOptions();
  expect(options.grid.vertLines.color).toBe(cssToken("border"));
  expect(options.grid.horzLines.color).toBe(cssToken("border"));
  expect(options.rightPriceScale.borderColor).toBe(cssToken("border-strong"));
  expect(options.layout.textColor).toBe(cssToken("muted"));
  expect(options.layout.background.color).toBe("rgba(0,0,0,0)");
  expect(options.autoSize).toBe(true);
});
