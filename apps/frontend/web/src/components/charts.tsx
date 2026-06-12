// The Tab-1 chart panels. The daily candlestick, the Greek term-structure line charts, and the
// smile use TradingView Lightweight Charts; only the 3D IV surface stays on the Plotly wrapper
// (lightweight-charts has no 3D/mesh path).
//
// Each panel is self-labelling (answers "what am I looking at?") and reads a typed BFF response.
// The 3D IV surface is a mesh3d over (delta, maturity, implied_vol); the smile is a 2D line chart
// of vol vs log-moneyness, split into its put wing (k ≤ 0) and call wing (k ≥ 0) as two series.

import type { Data } from "plotly.js";

import type { AnalyticsMaturity, AnalyticsPoint, PriceHistoryResponse, SmileAxis } from "../api";
import { CandleChart } from "./CandleChart";
import { CHART_COLORS } from "./chartTheme";
import { LightweightLineChart, type LightweightLineSeries } from "./LightweightLineChart";
import { Plot } from "./Plot";

// The smile x-axis values and their honest label (F-BFF-04): the rich projection serves
// signed deltas; the surface-grid fallback serves moneyness buckets and says so.
function smileAxis(smile: SmileAxis): { values: number[]; title: string } {
  if (smile.axis_type === "moneyness") {
    return { values: smile.moneyness_buckets, title: "moneyness (log)" };
  }
  return { values: smile.deltas, title: "delta" };
}

export function PriceChart({ data }: { data: PriceHistoryResponse }) {
  const label = `${data.underlying} — daily price (OHLC candlestick)`;
  if (data.n_bars === 0) {
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p>No daily bars for {data.underlying} in this window.</p>
      </figure>
    );
  }
  return <CandleChart bars={data.bars} label={label} />;
}

export function VolSurface({ maturities }: { maturities: AnalyticsMaturity[] }) {
  // A clean rectangular vol surface: x = the smile's strike axis (signed delta, or log-moneyness
  // on the grid fallback), y = the maturity *index* (0,1,2…), z = implied vol. The maturity axis
  // is an even index, not calendar years — in years the short tenors bunch near zero and the mesh
  // looks spiky/nonsensical; an even index lays the surface flat and regular. Plotly `surface`
  // over a (maturity × x) z-grid reads as a true surface, not a mesh3d scatter cloud. A missing
  // (x, maturity) cell is a null hole, never an invented value.
  const sorted = [...maturities]
    .filter((maturity) => smileAxis(maturity.smile).values.length > 0)
    .sort((a, b) => a.maturity_years - b.maturity_years);
  const axisTitle = sorted.length > 0 ? smileAxis(sorted[0].smile).title : "moneyness (log)";
  const label = `Implied-volatility surface (vol vs ${axisTitle} vs maturity)`;
  if (sorted.length === 0) {
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p>No surface to plot yet.</p>
      </figure>
    );
  }
  // Common x grid (union of every maturity's strike axis), so the z-grid stays rectangular even
  // if a maturity carries slightly different points.
  const xGrid = [...new Set(sorted.flatMap((m) => smileAxis(m.smile).values))].sort((a, b) => a - b);
  const z: (number | null)[][] = sorted.map((maturity) => {
    const axis = smileAxis(maturity.smile);
    const byX = new Map(axis.values.map((x, i) => [x, maturity.smile.implied_vols[i]]));
    return xGrid.map((x) => byX.get(x) ?? null);
  });
  const yIndex = sorted.map((_, i) => i);
  const trace: Data = {
    type: "surface",
    x: xGrid,
    y: yIndex,
    z,
    name: "IV surface",
    colorscale: "Viridis",
    showscale: false,
  };
  return (
    <Plot
      label={label}
      height={480}
      data={[trace]}
      layout={{
        scene: {
          xaxis: { title: { text: axisTitle } },
          yaxis: {
            title: { text: "maturity" },
            tickvals: yIndex,
            ticktext: sorted.map((maturity) => maturity.tenor_label || maturity.label),
          },
          zaxis: { title: { text: "implied vol" } },
          aspectmode: "cube",
        },
      }}
    />
  );
}

// Smile wing colours: puts (downside) read red, calls (upside) green — the convention an
// operator expects, read off the shared --negative / --positive design tokens.
const PUT_COLOR = CHART_COLORS.negative;
const CALL_COLOR = CHART_COLORS.positive;

// The smile is drawn in TradingView Lightweight Charts (the numeric-x yield-curve panel), not
// Plotly: it renders with a real height like the candlestick / term-structure panels, and the
// x-axis is log-moneyness so the curve reads as a true smile — ATM at 0, OTM puts on the left
// (strikes below the forward, k < 0), OTM calls on the right (k > 0). The downward left→right
// slope IS the skew. Two series — a red put wing (k ≤ 0) and a green call wing (k ≥ 0), sharing
// the ATM point so they join. The yield-curve scale rejects negative x, so log-moneyness is
// shifted +1.0 and scaled ×1000 into the integer axis; the tick formatter restores the real k.
const SMILE_X_OFFSET = 1.0;
const SMILE_X_SCALE = 1000;
const toSmileX = (k: number): number => Math.round((k + SMILE_X_OFFSET) * SMILE_X_SCALE);
const fromSmileX = (x: number): string => (x / SMILE_X_SCALE - SMILE_X_OFFSET).toFixed(3);

export function SmileChart({ maturity }: { maturity: AnalyticsMaturity }) {
  // A degenerate calibration (parameter railed to a bound, non-converged, or arb-breached)
  // is shown flagged, never as a clean fit — the flag clears once real term structure lands.
  const degenerate = maturity.surface_slice?.degenerate ?? false;
  const label = `Smile — ${maturity.label} (implied vol vs log-moneyness; puts ◄ ATM ► calls)${
    degenerate ? " ⚠ degenerate fit" : ""
  }`;
  const ks = maturity.smile.log_moneyness;
  const vols = maturity.smile.implied_vols;
  const puts: LightweightLineSeries = { label: "puts", color: PUT_COLOR, points: [] };
  const calls: LightweightLineSeries = { label: "calls", color: CALL_COLOR, points: [] };
  ks.forEach((k, i) => {
    const point = { x: toSmileX(k), label: k.toFixed(3), value: vols[i] };
    if (k <= 0) puts.points.push(point);
    if (k >= 0) calls.points.push(point);
  });
  return (
    <LightweightLineChart
      label={label}
      series={[puts, calls]}
      yUnit="IV"
      xFormatter={fromSmileX}
      valueFormatter={(value) => `${(value * 100).toFixed(1)}%`}
    />
  );
}

// The four dollar-Greeks graphed as a term structure — the curve view of the same
// projected_analytics the DollarGreeks table reads (so it fills and degrades identically:
// empty until the projection grid is populated). One panel per Greek, one line per delta band,
// x = maturity. The $ unit string is read off the payload (ADR 0036), shown as the y-axis title.
type GreekName = keyof AnalyticsPoint["metrics"];

const GREEK_PANELS: ReadonlyArray<{ name: GreekName; title: string }> = [
  { name: "delta", title: "Delta $" },
  { name: "gamma", title: "Gamma $" },
  { name: "vega", title: "Vega $" },
  { name: "theta", title: "Theta $" },
];

const BAND_COLORS = [
  CHART_COLORS.positive,
  "#8fc7ff",
  "#f0cf7a",
  "#d6b3ff",
  CHART_COLORS.negative,
  "#79d7cf",
] as const;

// Distinct delta bands across all maturities, ordered by their signed target delta (put → call)
// so the legend reads left-to-right the way the smile does.
function orderedBands(maturities: AnalyticsMaturity[]): string[] {
  const target = new Map<string, number>();
  for (const m of maturities) {
    for (const p of m.points) {
      if (!target.has(p.delta_band)) target.set(p.delta_band, p.target_delta);
    }
  }
  return [...target.entries()].sort((a, b) => a[1] - b[1]).map(([band]) => band);
}

function maturityMonths(maturity: AnalyticsMaturity): number {
  return Math.max(1, Math.round(maturity.maturity_years * 12));
}

// One line series per band: (maturity in months, dollar value) for the points that carry a
// non-null dollar (an older partition can carry a null $; it is skipped, never plotted as 0).
function bandSeries(maturities: AnalyticsMaturity[], greek: GreekName): LightweightLineSeries[] {
  return orderedBands(maturities)
    .map((band, index): LightweightLineSeries => {
      const points = maturities.flatMap((maturity) => {
        const point = maturity.points.find((p) => p.delta_band === band);
        const dollar = point?.metrics[greek].dollar;
        if (point === undefined || dollar === null || dollar === undefined) return [];
        return [{ x: maturityMonths(maturity), label: maturity.tenor_label, value: dollar }];
      });
      return { label: band, color: BAND_COLORS[index % BAND_COLORS.length], points };
    })
    .filter((series) => series.points.length > 0);
}

// The first non-null unit string for a Greek, to title that panel's y-axis. Falls back to "$".
function unitFor(maturities: AnalyticsMaturity[], greek: GreekName): string {
  for (const m of maturities) {
    for (const p of m.points) {
      const unit = p.metrics[greek].unit;
      if (unit) return unit;
    }
  }
  return "$";
}

export function GreeksTermStructure({ maturities }: { maturities: AnalyticsMaturity[] }) {
  const label = "Dollar Greeks term structure ($ value vs maturity, by delta band)";
  const sorted = [...maturities].sort((a, b) => a.maturity_years - b.maturity_years);
  if (!sorted.some((m) => m.points.length > 0)) {
    return (
      <section aria-label={label} className="greeks-term-structure">
        <h3>{label}</h3>
        <p>No projected analytics for this ticker/date yet.</p>
      </section>
    );
  }
  return (
    <section aria-label={label} className="greeks-term-structure">
      <h3>{label}</h3>
      <div className="chart-grid">
        {GREEK_PANELS.map(({ name, title }) => {
          const unit = unitFor(sorted, name);
          return (
            <LightweightLineChart
              key={name}
              label={`${title} term structure (${unit})`}
              series={bandSeries(sorted, name)}
              yUnit={unit}
            />
          );
        })}
      </div>
    </section>
  );
}
