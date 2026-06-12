// The Tab-1 chart panels. The daily candlestick and the Greek term-structure line charts use
// TradingView Lightweight Charts; the 3D IV surface and the smile stay on the Plotly wrapper.
//
// Each panel is self-labelling (answers "what am I looking at?") and reads a typed BFF response.
// The 3D IV surface is a mesh3d over (delta, maturity, implied_vol); the smile is a 2D scatter
// of vol vs delta.

import type { Data } from "plotly.js";

import type { AnalyticsMaturity, AnalyticsPoint, PriceHistoryResponse, SmileAxis } from "../api";
import { CandleChart } from "./CandleChart";
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
  // The x-axis label follows the payload's declared axis (delta-band grid vs the coarser
  // moneyness-bucket fallback) — mixed-axis maturities never happen (one source per day).
  const axisTitle = maturities.length > 0 ? smileAxis(maturities[0].smile).title : "delta";
  const label = `Implied-volatility surface (vol vs ${axisTitle} vs maturity)`;
  // Flatten every maturity's band points into an (x, maturity, vol) point cloud for mesh3d.
  const xs: number[] = [];
  const years: number[] = [];
  const vols: number[] = [];
  for (const maturity of maturities) {
    smileAxis(maturity.smile).values.forEach((x, i) => {
      xs.push(x);
      years.push(maturity.maturity_years);
      vols.push(maturity.smile.implied_vols[i]);
    });
  }
  if (vols.length === 0) {
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p>No surface to plot yet.</p>
      </figure>
    );
  }
  const trace: Data = {
    type: "mesh3d",
    x: xs,
    y: years,
    z: vols,
    name: "IV surface",
  };
  return (
    <Plot
      label={label}
      data={[trace]}
      layout={{
        scene: {
          xaxis: { title: { text: axisTitle } },
          yaxis: { title: { text: "maturity (y)" } },
          zaxis: { title: { text: "implied vol" } },
        },
      }}
    />
  );
}

export function SmileChart({ maturity }: { maturity: AnalyticsMaturity }) {
  const axis = smileAxis(maturity.smile);
  // A degenerate calibration (parameter railed to a bound, non-converged, or arb-breached)
  // is shown flagged, never as a clean fit — the flag clears once real term structure lands.
  const degenerate = maturity.surface_slice?.degenerate ?? false;
  const label = `Smile — ${maturity.label} (implied vol vs ${axis.title})${
    degenerate ? " ⚠ degenerate fit" : ""
  }`;
  const trace: Data = {
    type: "scatter",
    mode: "lines+markers",
    x: axis.values,
    y: maturity.smile.implied_vols,
    name: maturity.label,
  };
  return (
    <Plot
      label={label}
      data={[trace]}
      layout={{
        xaxis: { title: { text: axis.title } },
        yaxis: { title: { text: "implied vol" } },
      }}
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
  "#a8e6ba",
  "#8fc7ff",
  "#f0cf7a",
  "#d6b3ff",
  "#ef9c92",
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
