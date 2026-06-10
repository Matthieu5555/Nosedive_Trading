// The Tab-1 chart panels. The daily candlestick is on TradingView's lightweight-charts (see
// CandleChart); the 3D IV surface and the smile stay on the Plotly wrapper, which lightweight-
// charts cannot render (it draws 2D time series only).
//
// Each panel is self-labelling (answers "what am I looking at?") and reads a typed BFF response.
// The 3D IV surface is a mesh3d over (delta, maturity, implied_vol); the smile is a 2D scatter
// of vol vs delta.

import type { Data } from "plotly.js";

import type { AnalyticsMaturity, AnalyticsPoint, PriceHistoryResponse } from "../api";
import { CandleChart } from "./CandleChart";
import { Plot } from "./Plot";

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
  const label = "Implied-volatility surface (vol vs delta vs maturity)";
  // Flatten every maturity's band points into a (delta, maturity, vol) point cloud for mesh3d.
  const deltas: number[] = [];
  const years: number[] = [];
  const vols: number[] = [];
  for (const maturity of maturities) {
    maturity.smile.deltas.forEach((delta, i) => {
      deltas.push(delta);
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
    x: deltas,
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
          xaxis: { title: { text: "delta" } },
          yaxis: { title: { text: "maturity (y)" } },
          zaxis: { title: { text: "implied vol" } },
        },
      }}
    />
  );
}

export function SmileChart({ maturity }: { maturity: AnalyticsMaturity }) {
  const label = `Smile — ${maturity.label} (implied vol vs delta)`;
  const trace: Data = {
    type: "scatter",
    mode: "lines+markers",
    x: maturity.smile.deltas,
    y: maturity.smile.implied_vols,
    name: maturity.label,
  };
  return (
    <Plot
      label={label}
      data={[trace]}
      layout={{
        xaxis: { title: { text: "delta" } },
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

// One scatter trace per band: (maturity_years, dollar value) for the points that carry a
// non-null dollar (an older partition can carry a null $; it is skipped, never plotted as 0).
function bandSeries(maturities: AnalyticsMaturity[], greek: GreekName): Data[] {
  return orderedBands(maturities)
    .map((band): Data => {
      const x: number[] = [];
      const y: number[] = [];
      for (const m of maturities) {
        const point = m.points.find((p) => p.delta_band === band);
        const dollar = point?.metrics[greek].dollar;
        if (point && dollar !== null && dollar !== undefined) {
          x.push(m.maturity_years);
          y.push(dollar);
        }
      }
      return { type: "scatter", mode: "lines+markers", x, y, name: band };
    })
    .filter((trace) => ((trace as { x: number[] }).x.length > 0));
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
            <Plot
              key={name}
              label={`${title} term structure (${unit})`}
              data={bandSeries(sorted, name)}
              layout={{
                xaxis: { title: { text: "maturity (y)" } },
                yaxis: { title: { text: unit } },
              }}
            />
          );
        })}
      </div>
    </section>
  );
}
